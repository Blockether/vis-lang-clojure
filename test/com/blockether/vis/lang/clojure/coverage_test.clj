(ns com.blockether.vis.lang.clojure.coverage-test
  (:require [charred.api :as json]
            [clojure.java.io :as io]
            [clojure.string :as str]
            [com.blockether.vis.lang.clojure.coverage :as coverage]
            [com.blockether.vis.lang.clojure.test-runner :as tr]
            [lazytest.core :refer [defdescribe describe expect it]]))

(def ^:private run-via-cli @#'com.blockether.vis.lang.clojure.test-runner/run-via-cli)

(defn- temp-project
  "A temporary project with `files`, a map of relative path to text."
  [files]
  (let [root (str (java.nio.file.Files/createTempDirectory
                    "vis-coverage"
                    (make-array java.nio.file.attribute.FileAttribute 0)))]
    (doseq [[rel text] files]
      (let [f (io/file root rel)]
        (io/make-parents f)
        (spit f text)))
    root))

(def ^:private forms
  [{:ns "app.core"
    :file "app/core.clj"
    :line 3
    :kind "defn"
    :coords ["" "3" "3,1" "3,2"]
    :hits ["3" "3,2" "9"]}
   {:ns "app.core" :file "app/core.clj" :line 1 :kind "in-ns" :coords [""] :hits []}
   {:ns "app.core" :file "app/core.clj" :line 6 :kind "defmulti" :coords ["2"] :hits []}
   {:ns "app.core-test" :file "app/core_test.clj" :line 4 :kind "defn" :coords ["3"] :hits ["3"]}
   {:ns "app.shapes"
    :file "app/shapes.clj"
    :line 2
    :kind "defrecord"
    :coords ["4" "4,2" "5" "5,1"]
    :hits ["4" "4,2"]}])

(defdescribe
  instrument-command-test
  (it "adds the coverage alias and the hook and keeps the runner's own arguments"
      (let [cmd (coverage/instrument-command ["clojure" "-J-Xmx1g" "-M:test:dev" "--dir" "test"]
                                             ["app."])]
        (expect (= ["clojure" "-Sdeps"] (take 2 cmd)))
        (expect (= ["-J-Xmx1g" "-M:test:dev:vis-coverage" "--dir" "test" "--hook" coverage/hook-var]
                   (drop 3 cmd)))
        (expect (str/includes? (nth cmd 2) "-Dclojure.storm.instrumentOnlyPrefixes=app."))
        (expect (str/includes? (nth cmd 2) coverage/storm-version))))
  (it "refuses a command without an -M alias argument"
      (expect (nil? (coverage/instrument-command ["clojure" "-X:test"] ["app."])))))

(defdescribe default-prefixes-test
             (it "takes the shared prefix of each top-level segment from deps.edn :paths"
                 (let [root (temp-project {"deps.edn" "{:paths [\"src\" \"modules/x\"]}"
                                           "src/com/acme/a.clj" "(ns com.acme.a)"
                                           "src/com/acme/b/c.cljc" "(ns ^:no-doc com.acme.b.c)"
                                           "modules/x/demo/core.clj"
                                           ";; demo\n(ns demo.core\n  (:require [clojure.string]))"
                                           "test/com/acme/a_test.clj" "(ns zzz.a-test)"})]
                   (expect (= ["com.acme." "demo.core"] (coverage/default-prefixes root)))))
             (it "uses src without deps.edn, and gives nothing for an empty project"
                 (expect (= ["app.core"]
                            (coverage/default-prefixes (temp-project {"src/app/core.clj"
                                                                      "(ns app.core)"}))))
                 (expect (= [] (coverage/default-prefixes (temp-project {}))))))

(defdescribe report-test
             (let [report
                   (coverage/report forms ["app."])

                   rows
                   (get report "forms")]

               (it "keeps definitions outside test namespaces"
                   (expect (= "vis-coverage/1" (get report "format")))
                   (expect (= [["app/core.clj" 3] ["app/shapes.clj" 2]]
                              (mapv (juxt #(get % "file") #(get % "line")) rows))))
               (it "counts the instrumented expressions that ran, and each direct element"
                   (expect (= [4 2 {"3" [3 2]}]
                              ((juxt #(get % "hittable") #(get % "hit") #(get % "children"))
                                (first rows))))
                   (expect (= {"4" [2 2] "5" [2 0]} (get (second rows) "children"))))))

(def ^:private branchy
  "`(defn f [x] (if (pos? x) (inc x) (dec x)))` on lines 3-4, after a run with x = 1."
  {:ns "app.core"
   :file "app/core.clj"
   :line 3
   :kind "defn"
   :coords ["" "3" "3,1" "3,1,1" "3,2" "3,2,1" "3,3" "3,3,1"]
   :hits ["" "3" "3,1" "3,1,1" "3,2" "3,2,1"]
   :nodes {"" {:line 3 :head "defn" :size 4}
           "3" {:line 3 :head "if" :size 4}
           "3,1" {:line 3 :head "pos?" :size 2}
           "3,1,1" {:line 3}
           "3,2" {:line 3 :head "inc" :size 2}
           "3,2,1" {:line 3}
           "3,3" {:line 4 :head "dec" :size 2 :text "(dec x)"}
           "3,3,1" {:line 4 :text "x"}}})

(defdescribe gaps-test
             (it "names the outermost code that never ran, and the branch it is"
                 (expect (= [{"line" 4 "end_line" 4 "kind" "branch" "detail" "else: (dec x)"}]
                            (coverage/gaps branchy))))
             (it "gives a form that never ran as one gap over all its lines"
                 (expect (= [{"line" 3 "end_line" 4 "kind" "expression" "detail" "(defn f ...)"}]
                            (coverage/gaps (-> branchy
                                               (assoc :hits [])
                                               (assoc-in [:nodes "" :text] "(defn f ...)"))))))
             (it "gives nothing when every expression ran"
                 (expect (= [] (coverage/gaps (assoc branchy :hits (:coords branchy))))))
             (it "names the branch around code that has no coordinate, like the body of a do"
                 (let [form (-> branchy
                                (update :coords
                                        #(-> (vec (remove #{"3,3" "3,3,1"} %))
                                             (conj "3,3,1,1")))
                                (assoc-in [:nodes "3,3,1,1"]
                                          {:line 4 :head "println" :size 2 :text "(println x)"}))]
                   (expect (= [{"line" 4 "end_line" 4 "kind" "branch" "detail" "else: (println x)"}]
                              (coverage/gaps form)))
                   (expect (= [2 1] (coverage/branches form))))))

(defdescribe
  branches-test
  (it "counts the measured branches and the ones that ran"
      (expect (= [2 1] (coverage/branches branchy)))
      (expect (= [0 0] (coverage/branches (first forms)))))
  (it "knows the branches of each branching form"
      (expect (= ["then" "else" nil] (mapv #(coverage/branch-label "if-let" 4 %) [2 3 1])))
      (expect (= ["body" nil] (mapv #(coverage/branch-label "when" 4 %) [2 3])))
      (expect (= [nil "clause" nil "clause"] (mapv #(coverage/branch-label "cond" 5 %) [1 2 3 4])))
      (expect (= [nil "step" "step"] (mapv #(coverage/branch-label "cond->" 6 %) [2 3 5])))
      (expect (= ["clause" "clause" "default"] (mapv #(coverage/branch-label "case" 7 %) [3 5 6])))
      (expect (= ["clause" nil] (mapv #(coverage/branch-label "case" 6 %) [5 4])))
      (expect (nil? (coverage/branch-label "let" 3 2)))))

(defdescribe hook-source-test
             (it "is Clojure that reads"
                 (expect (= 'ns (ffirst (read-string (str "[" @#'coverage/hook-source "]")))))))

(defdescribe
  finish-test
  (it "reports a run that recorded nothing"
      (let [r (coverage/finish! (temp-project {}) ["app."])]
        (expect (str/includes? (get r "error") coverage/forms-path))
        (expect (= [] (get r "files")))))
  (it "writes the report and summarizes each file under the project root"
      (let [root
            (temp-project {"src/app/core.clj" "(ns app.core)" coverage/forms-path (pr-str forms)})

            r
            (coverage/finish! root ["app."])]

        (expect (= coverage/report-path (get r "report")))
        (expect (= [{"path" "app/shapes.clj" "covered" 2 "total" 4 "branches_total" 0}
                    {"path" "src/app/core.clj" "covered" 2 "total" 4 "branches_total" 0}]
                   (->> (get r "files")
                        (sort-by #(get % "path"))
                        (mapv #(select-keys % ["path" "covered" "total" "branches_total"])))))
        (expect (= "vis-coverage/1"
                   (get (json/read-json (slurp (io/file root coverage/report-path))) "format"))))))

(defdescribe
  run-via-cli-coverage-test
  (describe "a Lazytest project"
            (it "runs the instrumented command with the hook written and adds the coverage"
                (let [root
                      (temp-project {"src/app/core.clj" "(ns app.core)"})

                      seen
                      (atom nil)

                      r
                      (with-redefs [tr/cli-command-for
                                    (fn [_ _ _]
                                      {:tool :clj
                                       :framework :lazytest
                                       :selectors? true
                                       :cmd ["clojure" "-M:test"]})

                                    tr/run-command
                                    (fn [dir argv _]
                                      (reset! seen argv)
                                      (spit (io/file dir coverage/forms-path) (pr-str forms))
                                      {:exit 0
                                       :out "Ran 2 test cases in 0.1 seconds.\n0 failures."
                                       :err ""})]

                        (run-via-cli root {:coverage {:prefixes []}}))]

                  (expect (true? (get r "is_pass")))
                  (expect (= "-M:test:vis-coverage" (nth @seen 3)))
                  (expect (.isFile (io/file root coverage/hook-path "vis_coverage" "hook.clj")))
                  (expect (= ["app.core"] (get-in r ["coverage" "prefixes"])))
                  (expect (= 2 (count (get-in r ["coverage" "files"]))))
                  (expect (every? #(contains? % "gaps") (get-in r ["coverage" "files"]))))))
  (describe "another runner"
            (it "starts no tests"
                (let [ran
                      (atom false)

                      r
                      (with-redefs [tr/cli-command-for
                                    (fn [_ _ _]
                                      {:tool :clj
                                       :framework :cognitect
                                       :selectors? true
                                       :cmd ["clojure" "-M:test"]})

                                    tr/run-command
                                    (fn [& _]
                                      (reset! ran true)
                                      {:exit 0 :out "" :err ""})]

                        (run-via-cli (temp-project {}) {:coverage {:prefixes ["app."]}}))]

                  (expect (false? @ran))
                  (expect (str/includes? (get r "error") "coverage needs a Lazytest runner"))))))
