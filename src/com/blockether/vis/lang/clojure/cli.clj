(ns com.blockether.vis.lang.clojure.cli
  "The process the Python glue talks to: ONE JSON object per line in, one out.

   A project gets these tools by running this namespace with the Clojure CLI —

       clojure -Sdeps '{:deps {com.blockether/vis-lang-clojure {:mvn/version \"1.0.2\"}}}' \\
               -M -m com.blockether.vis.lang.clojure.cli

   — and keeping the process alive: managed nREPLs are ITS children, so a REPL
   started by one call is still there for the next one. Each verb runs in a lane
   ([[lanes]]). Verbs in different lanes run at the same time, so a lint or a format
   does not wait for a test run. One lane answers its requests in order. Answers can
   arrive out of order, and each answer carries the id of its request. EOF on stdin
   ends the run: the lanes finish their requests, then every REPL it started stops.

   Request:  {\"id\": \"7\", \"verb\": \"format\", \"root\": \"/proj\", \"session\": \"s1\", \"arg\": …}
   Answer:   {\"id\": \"7\", \"ok\": true, \"result\": …}
             {\"id\": \"7\", \"ok\": false, \"error\": {\"message\": …, \"hint\": …}}

   Verbs: `format`, `lint`, `test`, `repl-eval`, `repl` (with `op`), `ping`. A `ping`
   is answered at once, and its result names the lane of each verb."
  (:require [charred.api :as json]
            [clojure.string :as str]
            [com.blockether.vis.lang.clojure.api :as api]
            [com.blockether.vis.lang.clojure.repl-manager :as repl-manager]
            [com.blockether.vis.lang.clojure.test-runner :as test-runner])
  (:import (java.io BufferedReader Writer)
           (java.util.concurrent LinkedBlockingQueue))
  (:gen-class))

(def ^:private sessions
  "Every session id a request has named, so shutdown can stop what it started."
  (atom #{}))

(def lanes
  "The lane of each verb. Verbs in different lanes run at the same time. One lane runs
   its requests one after another, in the order they came. `test`, `repl-eval` and
   `repl` share a lane, because each one can use the same managed nREPL."
  {"format" "format" "lint" "lint" "test" "repl" "repl-eval" "repl" "repl" "repl"})

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
           {"id" id "ok" true "result" {"pong" true "lanes" lanes}}

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

(defn- answer-line
  "The JSON text of `answer`. Total: an answer that does not encode becomes an error
   answer under the same id, so the caller still gets an answer."
  ^String [answer]
  (try (json/write-json-str answer)
       (catch Throwable e
         (json/write-json-str {"id" (get answer "id") "ok" false "error" (error-data e)}))))

(defn- write-answer!
  "Write `answer` as one line. Each lane answers from its own thread, so a lock on
   `writer` keeps each line whole."
  [^Writer writer answer]
  (let [line (answer-line answer)]
    (locking writer (.write writer line) (.write writer "\n") (.flush writer))))

(defn- run-lane!
  "Answer the requests of one lane, one after another, until `::closed` arrives. A
   write that fails is reported on stderr, and the lane keeps serving."
  [^LinkedBlockingQueue queue writer]
  (loop []

    (let [request (.take queue)]
      (when-not (identical? ::closed request)
        (try (write-answer! writer (handle request))
             (catch Throwable e
               (.println System/err
                         (str "vis-lang-clojure: no answer for request " (get request "id")
                              ": " (.getMessage e)))))
        (recur)))))

(defn- serve!
  "Answer the requests from `reader` on `writer` until EOF. A verb with a lane waits in
   the queue of its lane, and any other request is answered at once. At EOF, each lane
   finishes its requests before this returns."
  [^BufferedReader reader writer]
  (let [queues
        (into {}
              (map (fn [lane]
                     [lane (LinkedBlockingQueue.)]))
              (distinct (vals lanes)))

        threads
        (mapv (fn [[lane queue]]
                (doto (Thread. ^Runnable (bound-fn [] (run-lane! queue writer))
                               (str "vis-lang-clojure-" lane))
                  (.setDaemon true)
                  (.start)))
              queues)]

    (loop []

      (when-let [line (.readLine reader)]
        (when-not (str/blank? line)
          (let [[request failure] (try [(json/read-json line) nil] (catch Throwable e [nil e]))
                queue (get queues (get lanes (str (get request "verb"))))]

            (cond failure (write-answer! writer {"ok" false "error" (error-data failure)})
                  queue (.put ^LinkedBlockingQueue queue request)
                  :else (write-answer! writer (handle request)))))
        (recur)))
    (doseq [^LinkedBlockingQueue queue (vals queues)]
      (.put queue ::closed))
    (doseq [^Thread thread threads]
      (.join thread))))

(defn -main
  "Serve requests from stdin until it closes, then stop every REPL this run started."
  [& _args]
  (.addShutdownHook (Runtime/getRuntime) (Thread. ^Runnable stop-repls!))
  (serve! (BufferedReader. *in*) *out*)
  (stop-repls!))
