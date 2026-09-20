(ns com.blockether.vis.lang.clojure.host
  "The host services the Clojure tools need: a clock, digests, home-relative paths, a
   log directory, environment fingerprints, child processes, and the result envelope.

   This library is STANDALONE. It runs as a plain JVM program (`clojure -M -m
   com.blockether.vis.lang.clojure.cli`), so nothing here may reach for an editor, an
   agent or a sandbox — everything below is java.lang and clojure.core."
  (:require [clojure.java.io :as io]
            [clojure.string :as str])
  (:import (java.lang ProcessHandle)
           (java.nio.charset StandardCharsets)
           (java.security MessageDigest)
           (java.time Instant ZoneOffset)
           (java.time.format DateTimeFormatter)))

(def RUN_TESTS_TIMEOUT_MS
  "The budget for ONE test run — ten minutes, shared by the nREPL path and the owned
   CLI process so a single deadline covers a cold start (JVM boot, namespace loading,
   compilation) and the run itself. Overrunning it is reported as a STRUCTURED result —
   the wedged REPL, the killed process, the output that did arrive — never as an opaque
   kill, so the number says when we stop believing the suite will finish."
  600000)

(defn now-ms
  "Wall-clock milliseconds. One spelling, so every timestamp in this library agrees."
  ^long []
  (System/currentTimeMillis))

(defn utf8 "UTF-8 bytes of `x`." ^bytes [x] (.getBytes (str x) StandardCharsets/UTF_8))

(defn sha256
  "SHA-256 digest bytes of `b`."
  ^bytes [^bytes b]
  (.digest (MessageDigest/getInstance "SHA-256") b))

(defn sha256-hex
  "Lowercase hex SHA-256 of `x`."
  ^String [x]
  (str/join (map #(format "%02x" %) (sha256 (utf8 x)))))

(defn abbreviate-home
  "`path` with the user's home directory collapsed to `~/`, which is how every path
   this library reports back reads. The home directory itself renders as `~/`; a path
   outside it passes through unchanged."
  ^String [path]
  (let [s
        (str path)

        home
        (System/getProperty "user.home")]

    (cond (or (str/blank? s) (str/blank? home)) s
          (= s home) "~/"
          (str/starts-with? s (str home "/")) (str "~/" (subs s (inc (count home))))
          :else s)))

(defn ensure-log-date-dir!
  "Create (once) and return today's log directory, `<tmp>/vis-lang-clojure/YYYY-MM-DD`
   in UTC. Subprocess logs live outside the project so a REPL boot log never lands in
   a working tree, and the date keeps a long-lived machine's logs sorted."
  (^String [] (ensure-log-date-dir! (Instant/now)))
  (^String [^Instant instant]
   (let [day
         (.format (.withZone (DateTimeFormatter/ofPattern "yyyy-MM-dd") ZoneOffset/UTC) instant)

         dir
         (io/file (System/getProperty "java.io.tmpdir") "vis-lang-clojure" day)]

     (.mkdirs dir)
     (.getAbsolutePath dir))))

(defn call-env-values
  "The environment delta a call named, as plain `{\"NAME\" value}`. Values cross the CLI
   as strings; a nil value means UNSET that variable for the child process."
  [env]
  (into (sorted-map)
        (map (fn [[k v]]
               [(str k) (when (some? v) (str v))]))
        (or env {})))

(defn env-fingerprint
  "`{NAME \"<digest>\"}` for one resolved delta — its SHAPE without its values. This is
   what a status prints and what a REUSED process is compared against, and both are
   read by a model and written to a log, so the value itself can never appear: a name
   set from a keychain must compare equal to itself and to nothing else. An unset name
   fingerprints as \"unset\"."
  [values]
  (into (sorted-map)
        (map (fn [[k v]]
               [(str k) (if (nil? v) "unset" (subs (sha256-hex (str v)) 0 12))]))
        values))

(defn env-difference
  "Variable NAMES whose value differs between the env a live process is running with
   and the one a new start asked for. Both sides are FINGERPRINTS, so this compares
   digests and answers names — the only thing either side may keep."
  [running requested]
  (vec (sort (into #{}
                   (remove (fn [k]
                             (= (get running k) (get requested k))))
                   (concat (keys running) (keys requested))))))

(defn env-mismatch-refusal
  "`{:message :differing}` when a REPL is already running with an env OTHER than the one
   this start named, else nil. A live REPL is reused, never silently replaced, and an
   env it was not started with is a different REPL. Names and digests only — a value
   never reaches it."
  [id running requested]
  (when-let [differing (seq (env-difference running requested))]
    {:differing (vec differing)
     :message (str "repl_start for " id
                   " is already running with a different env (" (str/join ", " differing)
                   "). There is no restart:"
                   " repl_stop that REPL, then start it with this env.")}))

(defn spawn!
  "Start `argv` in `dir` and answer its `Process`. `:env` adds or overrides environment
   variables for the child (a nil value REMOVES one) and `:merge-stderr?` folds stderr
   into stdout, so one pump captures a whole boot log in the order it was written."
  ^Process [argv ^String dir {:keys [env merge-stderr?]}]
  (let [pb (ProcessBuilder. ^java.util.List (mapv str argv))]
    (.directory pb (io/file dir))
    (when merge-stderr? (.redirectErrorStream pb true))
    (let [environment (.environment pb)]
      (doseq [[k v] (call-env-values env)]
        (if (nil? v) (.remove environment (str k)) (.put environment (str k) (str v)))))
    (.start pb)))

(defn kill-process-tree!
  "Destroy `process` and every process it started: TERM the descendants first, then the
   process, and after a two-second grace KILL whatever is left. Waits for the tree to
   actually exit, so a caller that reports \"terminated\" is telling the truth. Never
   throws, and never loses an interrupt — a cancelled test run still tears its
   subprocesses down."
  [^Process process]
  (when process
    (let [interrupted? (volatile! (Thread/interrupted))]
      (try
        ;; Snapshot the handles BEFORE terminating: a launcher can exit before its
        ;; children, and a dead parent no longer lists them.
        (let [handle (.toHandle process)
              descendants (with-open [stream (.descendants handle)]
                            (vec (iterator-seq (.iterator stream))))
              alive? (fn []
                       (or (.isAlive handle)
                           (boolean (some (fn [^ProcessHandle d]
                                            (.isAlive d))
                                          descendants))))
              await-exit! (fn []
                            (let [deadline (+ (System/nanoTime) 2000000000)]
                              (loop []

                                (when (and (alive?) (< (System/nanoTime) deadline))
                                  (try (Thread/sleep 50)
                                       (catch InterruptedException _ (vreset! interrupted? true)))
                                  (recur)))))]

          (run! (fn [^ProcessHandle d]
                  (try (.destroy d) (catch Throwable _ nil)))
                descendants)
          (try (.destroy process) (catch Throwable _ nil))
          (await-exit!)
          (run! (fn [^ProcessHandle d]
                  (try (when (.isAlive d) (.destroyForcibly d)) (catch Throwable _ nil)))
                descendants)
          (when (.isAlive process) (try (.destroyForcibly process) (catch Throwable _ nil)))
          (await-exit!))
        (catch Throwable _ nil)
        (finally (when @interrupted? (.interrupt (Thread/currentThread)))))))
  nil)

(defn success
  "A successful call envelope: `{:result … :success? true :error nil}`. The CLI turns it
   into the JSON one request answers with."
  [{:keys [result]}]
  {:result result :success? true :error nil})

(defn failure
  "A failed call envelope: `{:result nil :success? false :error {:message … :hint …}}`.
   A failure is DATA — an expected, actionable condition the caller can read and act on,
   not a stack trace."
  [{:keys [error]}]
  {:result nil :success? false :error error})
