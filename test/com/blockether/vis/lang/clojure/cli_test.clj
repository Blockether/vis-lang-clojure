(ns com.blockether.vis.lang.clojure.cli-test
  "Tests for the JSON-lines process the Python glue talks to: one request object in,
   one answer object out, a failure is an answer too, and EOF stops what it started."
  (:require [charred.api :as json]
            [clojure.java.io :as io]
            [clojure.string :as str]
            [com.blockether.vis.lang.clojure.api :as api]
            [com.blockether.vis.lang.clojure.cli :as cli]
            [com.blockether.vis.lang.clojure.repl-manager :as repl-manager]
            [com.blockether.vis.lang.clojure.test-runner :as test-runner]
            [lazytest.core :refer [defdescribe expect it]])
  (:import (java.nio.file Files)
           (java.nio.file.attribute FileAttribute)))

(defn- tmp-dir
  ^java.io.File []
  (.toFile (Files/createTempDirectory "vis-clj-cli-" (into-array FileAttribute []))))

(defn- cleanup
  [^java.io.File root]
  (when (.exists root)
    (doseq [^java.io.File f (reverse (file-seq root))]
      (.delete f))))

(defn- request
  "One request map, with the keys the protocol spells."
  [verb & {:as more}]
  (merge {"id" "1" "verb" verb "root" (System/getProperty "user.dir") "session" "cli-test"} more))

(defdescribe ping-test
             (it "answers a ping, so a client can wait for the process to be ready"
                 (expect (= {"id" "1" "ok" true "result" {"pong" true}}
                            (cli/handle (request "ping")))))
             (it "answers with the id it was asked under"
                 (expect (= "42" (get (cli/handle (request "ping" "id" "42")) "id")))))

(defdescribe unknown-verb-test
             (it "refuses a verb it does not have, and lists the ones it does"
                 (let [answer (cli/handle (request "reformat-everything"))]
                   (expect (false? (get answer "ok")))
                   (expect (str/includes? (get-in answer ["error" "message"])
                                          "reformat-everything"))
                   (expect (str/includes? (get-in answer ["error" "hint"]) "format"))
                   (expect (str/includes? (get-in answer ["error" "hint"]) "repl-eval")))))

(defdescribe
  format-verb-test
  (it "formats a code string end to end, through the same api the library exposes"
      (let [answer (cli/handle (request "format" "arg" {"code" "(defn f[x]   (inc x))"}))]
        (expect (true? (get answer "ok")))
        (expect (true? (get-in answer ["result" "changed"])))
        (expect (= "zprint" (get-in answer ["result" "formatter"])))))
  (it "reports already-formatted code as unchanged"
      (let [answer (cli/handle (request "format" "arg" {"code" "(defn f [x] (inc x))\n"}))]
        (expect (true? (get answer "ok")))
        (expect (false? (get-in answer ["result" "changed"]))))))

(defdescribe lint-verb-test
             (it "lints a code string and reports the finding"
                 (let [answer (cli/handle (request "lint" "arg" {"code" "(defn f [x] (inc y))"}))]
                   (expect (true? (get answer "ok")))
                   (expect (pos? (get-in answer ["result" "error"])))
                   (expect (seq (get-in answer ["result" "findings"])))
                   (expect (= "clojure" (get-in answer ["result" "language"])))))
             (it "reports clean code with no findings at all"
                 (let [answer (cli/handle (request "lint" "arg" {"code" "(def answer 42)\n"}))]
                   (expect (true? (get answer "ok")))
                   (expect (= 0 (get-in answer ["result" "error"])))
                   (expect (= 0 (get-in answer ["result" "warning"])))
                   (expect (empty? (get-in answer ["result" "findings"]))))))

(defdescribe
  routing-test
  (it "hands each verb its own tool, with the workspace root and session it named"
      (let [seen
            (atom [])

            record
            (fn [verb]
              (fn [env arg]
                (swap! seen conj [verb env arg])
                {:result {"ran" verb} :success? true}))]

        (with-redefs [api/clj-format-fn
                      (record "format")

                      api/clj-lint-fn
                      (record "lint")

                      test-runner/clj-test-fn
                      (record "test")

                      api/clj-eval-fn
                      (record "repl-eval")]

          (doseq [verb ["format" "lint" "test" "repl-eval"]]
            (expect (= {"ran" verb}
                       (get (cli/handle (request verb "root" "/proj" "arg" {"k" verb})) "result"))))
          (expect (= ["format" "lint" "test" "repl-eval"] (mapv first @seen)))
          (expect (= [{:workspace/root "/proj" :session-id "cli-test"}]
                     (distinct (mapv second @seen))))
          (expect (= [{"k" "format"} {"k" "lint"} {"k" "test"} {"k" "repl-eval"}]
                     (mapv last @seen))))))
  (it "passes the repl op through as its own positional argument"
      (let [seen (atom nil)]
        (with-redefs [api/repl-start-fn (fn [env op opts]
                                          (reset! seen [env op opts])
                                          {:result {"op" op} :success? true})]
          (expect (= {"op" "status"}
                     (get (cli/handle (request "repl" "op" "status" "arg" {"cwd" "/proj"}))
                          "result")))
          (expect (= [{:workspace/root (System/getProperty "user.dir") :session-id "cli-test"}
                      "status" {"cwd" "/proj"}]
                     @seen))))))

(defdescribe
  failure-answer-test
  (it "turns a failed envelope into ok=false with its message and hint"
      (with-redefs [api/clj-lint-fn (fn [_env _arg]
                                      {:result nil
                                       :success? false
                                       :error {:message "no such path" :hint "check the path"}})]
        (expect (= {"id" "1" "ok" false "error" {"message" "no such path" "hint" "check the path"}}
                   (cli/handle (request "lint"))))))
  (it "omits a hint the failure did not carry"
      (with-redefs [api/clj-lint-fn (fn [_env _arg]
                                      {:result nil :success? false :error {:message "nope"}})]
        (expect (= {"message" "nope"} (get (cli/handle (request "lint")) "error")))))
  (it "answers a THROWN error rather than taking the process down"
      (with-redefs [api/clj-format-fn (fn [_env _arg]
                                        (throw (ex-info "bad argument"
                                                        {:type :clj/bad-arg
                                                         :hint "pass code or path"})))]
        (let [answer (cli/handle (request "format"))]
          (expect (false? (get answer "ok")))
          (expect (= "bad argument" (get-in answer ["error" "message"])))
          (expect (= "pass code or path" (get-in answer ["error" "hint"])))
          (expect (= ":clj/bad-arg" (get-in answer ["error" "type"]))))))
  (it "names the class of an error that carried no message"
      (with-redefs [api/clj-format-fn (fn [_env _arg]
                                        (throw (NullPointerException.)))]
        (expect (= "java.lang.NullPointerException"
                   (get-in (cli/handle (request "format")) ["error" "message"]))))))

(defdescribe stop-repls!-test
             (it "stops what it started and detaches what it only attached"
                 (let [stopped
                       (atom [])

                       detached
                       (atom [])]

                   (with-redefs [repl-manager/session-repls
                                 (fn [session]
                                   (when (= "cli-test" session)
                                     [{:dir "/proj/a"} {:dir "/proj/b" :external? true}]))

                                 repl-manager/stop!
                                 (fn [session dir]
                                   (swap! stopped conj [session dir]))

                                 repl-manager/detach!
                                 (fn [session dir]
                                   (swap! detached conj [session dir]))]

                     (cli/handle (request "ping"))
                     (cli/stop-repls!)
                     (expect (= [["cli-test" "/proj/a"]] @stopped))
                     (expect (= [["cli-test" "/proj/b"]] @detached)))))
             (it "survives a session whose REPLs cannot be listed"
                 (with-redefs [repl-manager/session-repls (fn [_]
                                                            (throw (ex-info "gone" {})))]
                   (expect (nil? (cli/stop-repls!))))))

(defdescribe stdio-loop-test
             (it "answers one line per request, in order, and ends at EOF"
                 (let [out
                       (with-in-str (str (json/write-json-str {"id" "a" "verb" "ping"})
                                         "\n"
                                         "\n"
                                         (json/write-json-str {"id" "b" "verb" "nope"})
                                         "\n")
                                    (with-out-str (cli/-main)))

                       answers
                       (mapv json/read-json (str/split-lines (str/trim out)))]

                   (expect (= 2 (count answers)))
                   (expect (= {"id" "a" "ok" true "result" {"pong" true}} (first answers)))
                   (expect (= "b" (get (second answers) "id")))
                   (expect (false? (get (second answers) "ok")))))
             (it "answers a line that is not a request at all, and keeps serving"
                 (let [out
                       (with-in-str
                         (str "not json\n" (json/write-json-str {"id" "c" "verb" "ping"}) "\n")
                         (with-out-str (cli/-main)))

                       answers
                       (mapv json/read-json (str/split-lines (str/trim out)))]

                   (expect (= 2 (count answers)))
                   (expect (false? (get (first answers) "ok")))
                   (expect (string? (get-in (first answers) ["error" "message"])))
                   (expect (= {"id" "c" "ok" true "result" {"pong" true}} (second answers))))))

(defdescribe
  test-verb-test
  (it
    "runs a project's tests through the CLI, answering the same result the tool does"
    (let [root (tmp-dir)]
      (try
        (io/make-parents (io/file root "test" "sample" "core_test.clj"))
        (spit
          (io/file root "deps.edn")
          (str
            "{:paths [\"src\"]\n"
            " :deps {org.clojure/clojure {:mvn/version \"1.12.5\"}}\n"
            " :aliases {:test {:extra-paths [\"test\"]\n"
            "                  :extra-deps {io.github.noahtheduke/lazytest {:mvn/version \"2.1.0\"}}\n"
            "                  :main-opts [\"-m\" \"lazytest.main\" \"--dir\" \"test\"]}}}\n"))
        (spit (io/file root "test" "sample" "core_test.clj")
              (str "(ns sample.core-test\n"
                   "  (:require [lazytest.core :refer [defdescribe expect it]]))\n\n"
                   "(defdescribe arithmetic-test (it \"adds\" (expect (= 2 (+ 1 1)))))\n"))
        (let [answer (cli/handle (request "test" "root" (.getAbsolutePath root) "arg" {}))]
          (expect (true? (get answer "ok")))
          (expect (true? (get-in answer ["result" "is_pass"])))
          (expect (= 1 (get-in answer ["result" "total"])))
          (expect (= "cli" (get-in answer ["result" "mode"])))
          (expect (empty? (get-in answer ["result" "failures"]))))
        (finally (cleanup root))))))
