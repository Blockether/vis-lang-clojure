(ns com.blockether.vis.lang.clojure.verdicts-test
  "Checks the verdicts that the extension's Python reader must give against Clojure on
   this JVM.

   `reader_verdicts.json` holds Clojure source and where Clojure's own reader stops
   reading it. `regex_verdicts.json` holds regex patterns and the first line of Java's
   message for a pattern that does not compile. The Python tests check the extension
   against the same files, so both readers agree through them."
  (:require [charred.api :as charred]
            [clojure.string :as str]
            [lazytest.core :refer [defdescribe expect it]])
  (:import (clojure.lang LineNumberingPushbackReader LispReader$Resolver)
           (java.io File StringReader)
           (java.util.regex Pattern)))

(defn- cases
  "The cases of the fixture file `file-name`, which the Python tests share."
  [file-name]
  (charred/read-json (slurp (File. "extension/tests/fixtures" (str file-name)))))

(def ^:private resolver
  "Reads every alias as itself and every bare syntax-quoted symbol into `user`, so a
   verdict never depends on a namespace this process has not loaded."
  (reify
    LispReader$Resolver
      (currentNS [_] 'user)
      (resolveClass [_ _] nil)
      (resolveAlias [_ sym] sym)
      (resolveVar [_ _] nil)))

(defn- message
  "The first line of what `t` says, or its class name when it says nothing."
  [^Throwable t]
  (let [said (str (.getMessage t))]
    (if (str/blank? said) (.getSimpleName (class t)) (first (str/split-lines said)))))

(defn- on-a-line
  "`line` and `column` moved back to the end of the last line when the reader stopped
   past the end of `source`, where an editor shows no line."
  [^String source line column]
  (let [lines
        (str/split-lines source)

        n
        (count lines)]

    (if (and (pos? n) (> (long line) n)) [n (inc (count (peek lines)))] [line column])))

(defn- problem
  "Where `source` stops reading as Clojure, as `[line column message]`, or nil when it
   reads to the end. Line and column are 1-based. Nothing is evaluated or loaded:
   `*read-eval*` is off, an alias reads as written and an unknown tag reads as a
   tagged literal. Source nested too deeply for the reader's stack answers nil."
  [source]
  (let [rdr
        (LineNumberingPushbackReader. (StringReader. (str source)))

        opts
        {:read-cond :allow :eof ::eof}]

    (binding [*read-eval*
              false

              *reader-resolver*
              resolver

              *data-readers*
              {}

              *default-data-reader-fn*
              tagged-literal]

      (try (loop []

             (when-not (= ::eof (read opts rdr)) (recur)))
           (catch StackOverflowError _ nil)
           (catch Exception e
             (let [data
                   (ex-data e)

                   [line column]
                   (on-a-line (str source)
                              (or (:clojure.error/line data) (.getLineNumber rdr))
                              (or (:clojure.error/column data) (.getColumnNumber rdr)))]

               [line column (message (or (.getCause e) e))]))))))

(defn- pattern-problem
  "The first line of Java's message for `pattern`, or nil when it compiles."
  [pattern]
  (try (Pattern/compile (str pattern)) nil (catch Exception e (message e))))

(def ^:private stack-bytes
  "The stack that the verdicts read on. The fixture holds the verdict of a reader with
   enough stack, so a deep case must not depend on the stack size of the main thread."
  (* 512 1024 1024))

(defn- mismatches
  "Each case of `file-name` whose verdict on this JVM differs from the fixture, as
   `[input fixture-verdict jvm-verdict]`. The cases read on a thread with
   `stack-bytes` of stack, and a throw on that thread is thrown again here."
  [verdict-of input-key file-name]
  (let [found
        (promise)

        work
        (fn []
          (deliver found
                   (try (vec (for [c
                                   (cases file-name)

                                   :let [input
                                         (get c input-key)

                                         expected
                                         (get c "problem")

                                         actual
                                         (verdict-of input)]
                                   :when (not= expected actual)]

                               [input expected actual]))
                        (catch Throwable t t))))]

    (doto (Thread. nil ^Runnable work "verdicts" (long stack-bytes)) (.start) (.join))
    (let [result @found]
      (if (instance? Throwable result) (throw result) result))))

(defdescribe
  fixture-test
  (it "gives every reader verdict in reader_verdicts.json"
      (expect (= [] (vec (take 5 (mismatches problem "source" "reader_verdicts.json"))))))
  (it "gives every regex verdict in regex_verdicts.json"
      (expect (= [] (vec (take 5 (mismatches pattern-problem "pattern" "regex_verdicts.json"))))))
  (it "finds a mismatch when a fixture verdict is wrong"
      (expect (= [["(a" nil [1 3 "EOF while reading, starting at line 1"]]]
                 (with-redefs [cases (constantly [{"source" "(a" "problem" nil}])]
                   (vec (mismatches problem "source" "any.json")))))))
