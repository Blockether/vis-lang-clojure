(ns com.blockether.vis.lang.clojure.host-test
  "Tests for the host services every tool in this library shares: digests,
   home-relative paths, the log directory, environment fingerprints, child processes
   and the result envelope."
  (:require [clojure.java.io :as io]
            [clojure.string :as str]
            [com.blockether.vis.lang.clojure.host :as host]
            [lazytest.core :refer [defdescribe expect it]])
  (:import (java.lang ProcessHandle)
           (java.nio.file Files)
           (java.nio.file.attribute FileAttribute)
           (java.time Instant)))

(defn- tmp-dir
  ^java.io.File []
  (.toFile (Files/createTempDirectory "vis-clj-host-" (into-array FileAttribute []))))

(defn- cleanup
  [^java.io.File root]
  (when (.exists root)
    (doseq [^java.io.File f (reverse (file-seq root))]
      (.delete f))))

(defdescribe digest-test
             (it "hashes the same input to the same lowercase hex, and different inputs apart"
                 (expect (= (host/sha256-hex "abc") (host/sha256-hex "abc")))
                 (expect (not= (host/sha256-hex "abc") (host/sha256-hex "abd")))
                 (expect (= 64 (count (host/sha256-hex "abc"))))
                 (expect (some? (re-matches #"[0-9a-f]{64}" (host/sha256-hex "abc")))))
             (it "reads its input as UTF-8 bytes"
                 (expect (= 3 (count (host/utf8 "abc"))))
                 (expect (= 2 (count (host/utf8 "ł"))))
                 (expect (= 32 (count (host/sha256 (host/utf8 "abc")))))))

(defdescribe abbreviate-home-test
             (it "collapses the user's home to ~/ and leaves everything else alone"
                 (let [home (System/getProperty "user.home")]
                   (expect (= "~/" (host/abbreviate-home home)))
                   (expect (= "~/projects/app" (host/abbreviate-home (str home "/projects/app"))))
                   (expect (= "/opt/tools" (host/abbreviate-home "/opt/tools")))
                   ;; a sibling directory whose name merely STARTS with the home path is not inside it
                   (expect (= (str home "-backup") (host/abbreviate-home (str home "-backup"))))
                   (expect (= "" (host/abbreviate-home ""))))))

(defdescribe log-directory-test
             (it "creates today's UTC directory under the temp dir and answers it again"
                 (let [a
                       (host/ensure-log-date-dir! (Instant/parse "2026-03-04T23:30:00Z"))

                       f
                       (io/file a)]

                   (expect (.isDirectory f))
                   (expect (= "2026-03-04" (.getName f)))
                   (expect (= "vis-lang-clojure" (.getName (.getParentFile f))))
                   (expect (= a
                              (host/ensure-log-date-dir! (Instant/parse "2026-03-04T23:30:00Z"))))))
             (it "puts a later UTC day in its own directory"
                 (expect (not= (host/ensure-log-date-dir! (Instant/parse "2026-03-04T23:30:00Z"))
                               (host/ensure-log-date-dir! (Instant/parse "2026-03-05T00:30:00Z")))))
             (it "defaults to now, and stays outside any working tree"
                 (let [now (io/file (host/ensure-log-date-dir!))]
                   (expect (.isDirectory now))
                   (expect (str/starts-with? (.getAbsolutePath now)
                                             (.getAbsolutePath (io/file (System/getProperty
                                                                          "java.io.tmpdir"))))))))

(defdescribe call-env-values-test
             (it "renders a delta as sorted string names with string values"
                 (expect (= {"A" "1" "B" "two"} (host/call-env-values {"A" 1 "B" "two"})))
                 (expect (= ["A" "B" "C"] (keys (host/call-env-values {"C" "3" "A" "1" "B" "2"})))))
             (it "keeps a nil value, which means UNSET for the child"
                 (expect (= {"PAGER" nil} (host/call-env-values {"PAGER" nil}))))
             (it "answers an empty map for nothing at all"
                 (expect (= {} (host/call-env-values nil)))
                 (expect (= {} (host/call-env-values {})))))

(defdescribe env-fingerprint-test
             (it "never reveals a value: equal values fingerprint equal, different ones differ"
                 (let [secret (host/env-fingerprint {"TOKEN" "s3cret"})]
                   (expect (= secret (host/env-fingerprint {"TOKEN" "s3cret"})))
                   (expect (not= secret (host/env-fingerprint {"TOKEN" "other"})))
                   (expect (not (str/includes? (str secret) "s3cret")))
                   (expect (= 12 (count (get secret "TOKEN"))))))
             (it "fingerprints an unset name as \"unset\""
                 (expect (= {"PAGER" "unset"} (host/env-fingerprint {"PAGER" nil})))))

(defdescribe env-difference-test
             (it "answers only the NAMES whose fingerprints differ"
                 (let [running
                       (host/env-fingerprint (host/call-env-values {"A" "1" "B" "2"}))

                       same
                       (host/env-fingerprint (host/call-env-values {"A" "1" "B" "2"}))

                       other
                       (host/env-fingerprint (host/call-env-values {"A" "1" "B" "9" "C" "3"}))]

                   (expect (= [] (host/env-difference running same)))
                   (expect (= ["B" "C"] (host/env-difference running other))))))

(defdescribe env-mismatch-refusal-test
             (it "says nothing when the live REPL runs the env this start asked for"
                 (let [fp (host/env-fingerprint (host/call-env-values {"A" "1"}))]
                   (expect (nil? (host/env-mismatch-refusal "repl-1" fp fp)))))
             (it "refuses with the differing names and the way out, never a value"
                 (let [running
                       (host/env-fingerprint (host/call-env-values {"TOKEN" "live"}))

                       requested
                       (host/env-fingerprint (host/call-env-values {"TOKEN" "new"}))

                       refusal
                       (host/env-mismatch-refusal "repl-1" running requested)]

                   (expect (= ["TOKEN"] (:differing refusal)))
                   (expect (str/includes? (:message refusal) "repl-1"))
                   (expect (str/includes? (:message refusal) "repl_stop"))
                   (expect (not (str/includes? (:message refusal) "live"))))))

(defdescribe
  spawn-test
  (it "runs the command in the directory it was given"
      (let [root (tmp-dir)]
        (try (let [p (host/spawn! ["pwd"] (.getAbsolutePath root) {})]
               (.waitFor p)
               (expect (= (.getCanonicalPath root)
                          (.getCanonicalPath (io/file (str/trim (slurp (.getInputStream p))))))))
             (finally (cleanup root)))))
  (it "adds the env the call named and removes the ones it unset"
      (let [root (tmp-dir)]
        ;; `env` prints the child's environment as the child got it — no shell defaults
        ;; on top, so a REMOVED variable is visibly absent.
        (try (let [p (host/spawn! ["env"]
                                  (.getAbsolutePath root)
                                  {:env {"VIS_LANG_TEST" "present" "HOME" nil}})
                   _ (.waitFor p)
                   lines (str/split-lines (slurp (.getInputStream p)))]

               (expect (some #{"VIS_LANG_TEST=present"} lines))
               (expect (not-any? #(str/starts-with? % "HOME=") lines)))
             (finally (cleanup root)))))
  (it "keeps stderr separate unless the call asked for one stream"
      (let [root
            (tmp-dir)

            cmd
            ["bash" "-c" "printf out; printf err >&2"]]

        (try (let [split (host/spawn! cmd (.getAbsolutePath root) {})]
               (.waitFor split)
               (expect (= "out" (slurp (.getInputStream split))))
               (expect (= "err" (slurp (.getErrorStream split)))))
             (let [merged (host/spawn! cmd (.getAbsolutePath root) {:merge-stderr? true})]
               (.waitFor merged)
               (expect (= "outerr" (slurp (.getInputStream merged))))
               (expect (= "" (slurp (.getErrorStream merged)))))
             (finally (cleanup root))))))

(defdescribe kill-process-tree-test
             (it "terminates the process AND the children it started, and waits for their exit"
                 (let [root (tmp-dir)]
                   (try
                     (let [pid-file (io/file root "pids")
                           p (host/spawn!
                               ["bash" "-c"
                                (str "exec > pids; printf '%s\n' $$; "
                                     "bash -c 'sleep 30 & printf \"%s %s\\n\" $$ $!; wait' & wait")]
                               (.getAbsolutePath root)
                               {})
                           pids (loop [attempts 300]
                                  (let [pids (when (.exists pid-file)
                                               (mapv parse-long (re-seq #"\d+" (slurp pid-file))))]
                                    (if (or (= 3 (count pids)) (zero? attempts))
                                      pids
                                      (do (Thread/sleep 10) (recur (dec attempts))))))
                           handles (mapv #(.orElse (ProcessHandle/of (long %)) nil) pids)]

                       (expect (= 3 (count pids)))
                       (host/kill-process-tree! p)
                       (expect (not (.isAlive p)))
                       (doseq [^ProcessHandle handle handles]
                         (expect (or (nil? handle) (not (.isAlive handle))))))
                     (finally (cleanup root)))))
             (it "is a no-op for no process at all" (expect (nil? (host/kill-process-tree! nil)))))

(defdescribe envelope-test
             (it "wraps a value as a success, with no error"
                 (expect (= {:result {"ok" 1} :success? true :error nil}
                            (host/success {:result {"ok" 1}})))
                 (expect (= {:result nil :success? true :error nil} (host/success {}))))
             (it "wraps an error as a failure, with no result"
                 (expect (= {:result nil :success? false :error {:message "nope" :hint "try this"}}
                            (host/failure {:error {:message "nope" :hint "try this"}})))))
