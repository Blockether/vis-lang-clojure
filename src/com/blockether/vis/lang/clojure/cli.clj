(ns com.blockether.vis.lang.clojure.cli
  "The process the Python glue talks to: ONE JSON object per line in, one out.

   A project gets these tools by running this namespace with the Clojure CLI —

       clojure -Sdeps '{:deps {com.blockether/vis-lang-clojure {:mvn/version \"1.0.1\"}}}' \\
               -M -m com.blockether.vis.lang.clojure.cli

   — and keeping the process alive: managed nREPLs are ITS children, so a REPL
   started by one call is still there for the next one. Requests are answered in
   order, and EOF on stdin ends the run after stopping every REPL it started.

   Request:  {\"id\": \"7\", \"verb\": \"format\", \"root\": \"/proj\", \"session\": \"s1\", \"arg\": …}
   Answer:   {\"id\": \"7\", \"ok\": true, \"result\": …}
             {\"id\": \"7\", \"ok\": false, \"error\": {\"message\": …, \"hint\": …}}

   Verbs: `format`, `lint`, `test`, `repl-eval`, `repl` (with `op`), `ping`."
  (:require [charred.api :as json]
            [clojure.string :as str]
            [com.blockether.vis.lang.clojure.api :as api]
            [com.blockether.vis.lang.clojure.repl-manager :as repl-manager]
            [com.blockether.vis.lang.clojure.test-runner :as test-runner])
  (:import (java.io BufferedReader))
  (:gen-class))

(def ^:private sessions
  "Every session id a request has named, so shutdown can stop what it started."
  (atom #{}))

(defn- error-data
  "The error a failed call reports: the message, and the hint the thrower attached.
   A stack trace is never part of an answer — a caller acts on the sentence."
  [^Throwable e]
  (let [data (ex-data e)]
    (cond-> {"message" (or (not-empty (str (.getMessage e))) (.getName (class e)))}
      (:hint data)
      (assoc "hint" (str (:hint data)))

      (:type data)
      (assoc "type" (str (:type data))))))

(defn- envelope->answer
  "Turn one tool envelope (`{:result :success? :error}`) into the answer map."
  [id envelope]
  (if (:success? envelope)
    {"id" id "ok" true "result" (:result envelope)}
    {"id" id
     "ok" false
     "error" (let [{:keys [message hint]} (:error envelope)]
               (cond-> {"message" (str message)}
                 hint
                 (assoc "hint" (str hint))))}))

(defn handle
  "Run ONE request and answer it. Total: a thrown exception is an answer too, so a
   bad argument never takes the process down with the request that made it."
  [request]
  (let [id
        (get request "id")

        verb
        (str (get request "verb"))

        arg
        (get request "arg")

        env
        {:workspace/root (get request "root") :session-id (get request "session")}]

    (swap! sessions conj (get request "session"))
    (try (case verb
           "ping"
           {"id" id "ok" true "result" {"pong" true}}

           "format"
           (envelope->answer id (api/clj-format-fn env arg))

           "lint"
           (envelope->answer id (api/clj-lint-fn env arg))

           "test"
           (envelope->answer id (test-runner/clj-test-fn env arg))

           "repl-eval"
           (envelope->answer id (api/clj-eval-fn env arg))

           "repl"
           (envelope->answer id (api/repl-start-fn env (get request "op") arg))

           {"id" id
            "ok" false
            "error" {"message" (str "unknown verb " (pr-str verb))
                     "hint" "verbs: ping, format, lint, test, repl-eval, repl"}})
         (catch Throwable e {"id" id "ok" false "error" (error-data e)}))))

(defn stop-repls!
  "Stop every managed nREPL this process started, and detach every external one it
   attached. Called on the way out, so a client that goes away does not leave JVMs
   behind."
  []
  (doseq [session
          @sessions

          {:keys [dir external?]}
          (try (repl-manager/session-repls session) (catch Throwable _ nil))]

    (try (if external? (repl-manager/detach! session dir) (repl-manager/stop! session dir))
         (catch Throwable _ nil))))

(defn -main
  "Serve requests from stdin until it closes."
  [& _args]
  (.addShutdownHook (Runtime/getRuntime) (Thread. ^Runnable stop-repls!))
  (let [reader
        (BufferedReader. *in*)

        writer
        *out*]

    (loop []

      (if-let [line (.readLine reader)]
        (do (when-not (str/blank? line)
              (let [answer (try (handle (json/read-json line))
                                (catch Throwable e {"ok" false "error" (error-data e)}))]
                (.write writer (str (json/write-json-str answer) "\n"))
                (.flush writer)))
            (recur))
        (stop-repls!)))))
