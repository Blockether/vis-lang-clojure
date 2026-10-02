(ns com.blockether.vis.lang.clojure.syntax
  "Whether Clojure source reads, as decided by Clojure's own reader.

   The compiler uses the same reader, so the verdict is the language's verdict on
   unbalanced delimiters, unterminated strings, bad escapes and characters, invalid
   tokens and numbers, odd maps and duplicate keys. Nothing is evaluated or loaded:

   - `*read-eval*` is off, so `#=` and a record literal such as `#my.Rec{}` are
     reported instead of evaluated.
   - An alias in `::alias/k`, `#::alias{}` or a syntax-quoted symbol reads as
     written, because the file's `ns` form is never loaded.
   - An unknown tagged literal reads as a tagged-literal value. `#inst` and `#uuid`
     keep their own readers, which refuse a malformed value.
   - Reader conditionals are allowed in every file. Branches for other platforms
     are still read, so their delimiters, strings and literals are checked too.

   A regex literal compiles as a Java pattern, so a pattern that only JavaScript
   accepts is reported even in a `.cljs` file."
  (:require [clojure.string :as str])
  (:import (clojure.lang LineNumberingPushbackReader LispReader$Resolver)
           (java.io StringReader)))

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

(defn problem
  "Where `source` stops reading as Clojure: `{:line :column :message}`, or nil when it
   reads to the end. Line and column are 1-based and mark where the reader stopped.
   For a form still open at the end of the source that is the end, and the message
   names the line the form started on. Source too deeply nested for the reader's
   stack answers nil: there is no verdict to report."
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

               {:line line :column column :message (message (or (.getCause e) e))}))))))

(defn parses-clean?
  "True when `source` reads as Clojure from end to end; see `problem`."
  [source]
  (nil? (problem source)))

(defn check
  "The problem of every `[path source]` pair that does not read, in path order, as
   `{:path :line :column :message}`."
  [sources]
  (->> sources
       (keep (fn [[path source]]
               (some-> (problem source)
                       (assoc :path (str path)))))
       (sort-by (juxt :path :line :column))
       (vec)))
