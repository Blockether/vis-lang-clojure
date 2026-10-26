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
        (expect (= [{"path" "app/shapes.clj" "covered" 2 "total" 4}
                    {"path" "src/app/core.clj" "covered" 2 "total" 4}]
                   (sort-by #(get % "path") (get r "files"))))
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
                  (expect (= 2 (count (get-in r ["coverage" "files"])))))))
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
