(ns com.blockether.vis.lang.clojure.coverage
  "Test coverage for Clojure: the project's Lazytest run under ClojureStorm and
   Clofidence.

   ClojureStorm is a Clojure compiler that reports each expression it runs, and
   Clofidence records those reports. A coverage run adds both to the project's own
   test command through one extra deps.edn alias, `:vis-coverage`. A Lazytest hook
   in that alias writes the recorded forms as EDN, because the test JVM has no JSON
   library. This process then turns them into the `vis-coverage/1` JSON report that
   `graphify crap` reads:

       {\"format\": \"vis-coverage/1\", \"tool\": \"clofidence\", \"prefixes\": [...],
        \"forms\": [{\"file\": \"app/core.clj\", \"ns\": \"app.core\", \"line\": 3,
                    \"kind\": \"defn\", \"hittable\": 12, \"hit\": 9,
                    \"children\": {\"4\": [5, 5]}}]}

   `file` is classpath-relative. `hittable` counts the expressions the compiler
   instrumented in the top-level form, and `hit` the ones that ran. `children`
   gives the same counts for each direct element of the form, keyed by its index,
   so a tool can score each method of a `defrecord` or an `extend-type`.

   Each form also has `branches`, `[measured ran]` over the branches of its `if`,
   `when`, `cond`, `cond->` and `case` forms, and `gaps`. A gap is the outermost
   code that never ran inside code that did: `{\"line\", \"end_line\", \"kind\",
   \"detail\"}`, where `kind` is `branch` or `expression`."
  (:require [charred.api :as json]
            [clojure.edn :as edn]
            [clojure.java.io :as io]
            [clojure.set :as set]
            [clojure.string :as str]))

(def storm-version
  "The ClojureStorm compiler release, a drop-in replacement for org.clojure/clojure."
  "1.12.5-1")

(def clofidence-version "The Clofidence release that records the coverage." "0.5.0")

(def forms-path
  "Where the hook writes the recorded forms, under the project root."
  "target/vis-coverage/forms.edn")

(def hook-path
  "The source path that holds the hook namespace, under the project root."
  "target/vis-coverage/src")

(def report-path
  "The `vis-coverage/1` report, under the project root."
  "target/coverage/clofidence.json")

(def hook-var "The Lazytest hook that records coverage." "vis-coverage.hook/clofidence")

(def ^:private hook-source
  "The hook namespace. It runs inside the project's test JVM, so it uses only
   Clojure, Lazytest and Clofidence."
  ";; Written by vis-lang-clojure for a coverage run. Each run writes it again.
(ns vis-coverage.hook
  (:require [clojure.java.io :as io]
            [lazytest.hooks :as hooks]))

(defn- head [form]
  (when (and (seq? form) (symbol? (first form)))
    (name (first form))))

(defn- step [s]
  (try (Long/parseLong s) (catch NumberFormatException _ s)))

(defn- clip [x]
  (let [s (binding [*print-length* 6 *print-level* 6 *print-meta* false] (pr-str x))]
    (if (> (count s) 100) (str (subs s 0 97) \"...\") s)))

(defn- nodes
  \"For each coordinate: the nearest source line, the head and size of a call, and
   the code of an expression that never ran.\"
  [form coords hit]
  (let [at (requiring-resolve 'hansel.utils/get-form-at-coord)]
    (into {}
          (for [c coords
                :let [path (if (= \"\" c) [] (map step (.split ^String c \",\")))
                      [sub line] (reduce (fn [[f l] s]
                                           (let [g (at f [s])]
                                             [g (or (:line (meta g)) l)]))
                                         [form (:line (meta form))]
                                         path)
                      h (head sub)]]
            [c (cond-> {:line line}
                 h (assoc :head h :size (count sub))
                 (not (contains? hit c)) (assoc :text (clip sub)))]))))

(defn- forms []
  (let [registered ((requiring-resolve 'clofidence.storm/registered-forms))
        hits ((requiring-resolve 'clofidence.tracer/immutable-coords-coverage))]
    (vec (for [f registered
               :let [form (:form/form f)
                     coords (vec (or (:form/emitted-coords f)
                                     (:clojure.storm/emitted-coords (meta form))))
                     hit (set (get hits (:form/id f)))]
               :when (and (:form/file f) (seq coords))]
           {:ns (str (:form/ns f))
            :file (str (:form/file f))
            :line (or (:form/line f) 0)
            :kind (head form)
            :coords coords
            :hits (vec hit)
            :nodes (nodes form coords hit)}))))

(hooks/defhook clofidence
  (pre-test-run [_ m] (require 'clofidence.storm) m)
  (post-test-run [_ m]
    (let [out (io/file (System/getProperty \"vis.coverage.forms\"))]
      (io/make-parents out)
      (binding [*print-length* nil *print-level* nil *print-meta* false]
        (spit out (pr-str (forms)))))
    m))
")

(defn- source-dirs
  "The project's source directories: deps.edn `:paths`, or `src`."
  [root]
  (let [deps
        (io/file root "deps.edn")

        paths
        (when (.isFile deps) (try (:paths (edn/read-string (slurp deps))) (catch Exception _ nil)))]

    (->> (or (seq (filter string? paths)) ["src"])
         (map #(io/file root %))
         (filter #(.isDirectory ^java.io.File %)))))

(defn- ns-name-of
  "The namespace a source file declares, read from its `ns` form."
  [^java.io.File file]
  (second (re-find #"\(ns\s+(?:\^\S+\s+)*([^\s()\[\]{}\"]+)" (slurp file))))

(defn- source-nses
  [root]
  (->> (source-dirs root)
       (mapcat file-seq)
       (filter #(re-find #"\.clj[c]?$" (.getName ^java.io.File %)))
       (keep ns-name-of)
       (distinct)
       (sort)))

(defn- common-prefix
  "The namespace prefix all of `nses` share, by whole segments."
  [nses]
  (let [parts
        (map #(str/split % #"\.") nses)

        shared
        (reduce (fn [acc p]
                  (vec (map first
                            (take-while (fn [[a b]]
                                          (= a b))
                                        (map vector acc p)))))
                (first parts)
                (rest parts))]

    (cond (= 1 (count nses)) (first nses)
          (seq shared) (str (str/join "." shared) ".")
          :else (first nses))))

(defn default-prefixes
  "The namespace prefixes to instrument: for each top-level segment of the
   project's source namespaces, the prefix that its namespaces share."
  [root]
  (->> (source-nses root)
       (group-by #(first (str/split % #"\.")))
       (vals)
       (mapv common-prefix)
       (sort)
       (vec)))

(defn alias-edn
  "The `-Sdeps` value that adds the `:vis-coverage` alias."
  [prefixes]
  (pr-str {:aliases
           {:vis-coverage
            {:classpath-overrides {'org.clojure/clojure nil}
             :extra-paths [hook-path]
             :extra-deps {'com.github.flow-storm/clojure {:mvn/version storm-version}
                          'com.github.flow-storm/clofidence {:mvn/version clofidence-version}}
             :jvm-opts ["-Dclojure.storm.instrumentEnable=true"
                        "-Dclojure.storm.instrumentAutoPrefixes=false"
                        (str "-Dclojure.storm.instrumentOnlyPrefixes=" (str/join "," prefixes))
                        (str "-Dvis.coverage.forms=" forms-path)]}}}))

(defn instrument-command
  "`cmd`, a `clojure ... -M:aliases args...` vector, run under the coverage alias
   with the hook. Nil when `cmd` has no `-M` alias argument."
  [cmd prefixes]
  (let [[program & more]
        cmd

        idx
        (first (keep-indexed (fn [i a]
                               (when (str/starts-with? (str a) "-M") i))
                             more))]

    (when idx
      (let [more
            (vec more)

            m
            (str (nth more idx))]

        (-> [program "-Sdeps" (alias-edn prefixes)]
            (into (subvec more 0 idx))
            (conj (str m ":vis-coverage"))
            (into (subvec more (inc idx)))
            (into ["--hook" hook-var]))))))

(defn prepare!
  "Write the hook namespace and remove the forms of an earlier run."
  [root]
  (let [hook (io/file root hook-path "vis_coverage" "hook.clj")]
    (io/make-parents hook)
    (spit hook hook-source)
    (io/delete-file (io/file root forms-path) true)))

(defn- child-index
  "The direct element of the top-level form that `coord` is in, or nil for the form."
  [coord]
  (when-not (str/blank? coord) (first (str/split coord #","))))

(def ^:private measured-kinds
  "The top-level forms a report keeps: definitions of code that tests can run. A
   namespace's `ns` expansion and a `defmulti` dispatch only run at load time,
   before the hook records anything."
  #{"def" "defn" "defn-" "defmethod" "defonce" "defrecord" "deftype" "extend-protocol"
    "extend-type"})

(defn- measured?
  "Whether a recorded form belongs in the report: a definition outside a test namespace."
  [{:keys [ns kind]}]
  (and (contains? measured-kinds kind) (not (str/ends-with? (str ns) "-test"))))

(defn- parent-coord
  "The coordinate of the expression that holds `coord`, or nil for the form itself."
  [coord]
  (when-not (str/blank? coord)
    (if-let [i (str/last-index-of coord ",")]
      (subs coord 0 i)
      "")))

(defn- step-index
  "The position of `coord` in its parent call, or nil inside a map or a set."
  [coord]
  (parse-long (subs coord (inc (long (or (str/last-index-of coord ",") -1))))))

(defn- inside?
  "Whether `coord` is inside the expression at `outer`."
  [outer coord]
  (and (not= outer coord) (or (= "" outer) (str/starts-with? coord (str outer ",")))))

(defn branch-label
  "The branch that the element at `idx` is in a call of `head` with `size`
   elements: then, else, body, clause, step or default. Nil for other elements."
  [head size idx]
  (let [idx
        (long idx)

        size
        (long size)]

    (case head
      ("if" "if-not" "if-let" "if-some")
      (case idx
        2
        "then"

        3
        "else"

        nil)

      ("when" "when-not" "when-let" "when-some" "when-first")
      (when (= 2 idx) "body")

      "cond"
      (when (and (>= idx 2) (even? idx)) "clause")

      ("cond->" "cond->>")
      (when (and (>= idx 3) (odd? idx)) "step")

      "case"
      (cond (and (odd? (- size 2)) (= idx (dec size))) "default"
            (and (>= idx 3) (odd? idx)) "clause")

      nil)))

(defn- branch-of
  "The branch label of `coord` in its parent call, or nil."
  [nodes coord]
  (when-let [parent (parent-coord coord)]
    (let [{:keys [head size]} (get nodes parent)
          idx (step-index coord)]

      (when (and head idx) (branch-label head (or size 0) idx)))))

(defn gaps
  "The code of one recorded form that never ran: each expression that did not run
   inside one that did, so each gap names the outermost code that the tests missed."
  [{:keys [coords hits nodes line]}]
  (let [coords
        (set (map str coords))

        hit
        (set (map str hits))

        ran-around?
        (fn [c]
          (loop [a (parent-coord c)]
            (cond (nil? a) true
                  (contains? coords a) (contains? hit a)
                  :else (recur (parent-coord a)))))]

    (->> (sort coords)
         (remove hit)
         (filter ran-around?)
         (map
           (fn [c]
             (let [node
                   (get nodes c)

                   start
                   (long (or (:line node) line 0))

                   label
                   (loop [at c]
                     (or (branch-of nodes at)
                         (let [up (parent-coord at)]
                           (when (and up (not (contains? coords up))) (recur up)))))

                   text
                   (str (:text node))]

               {"line" start
                "end_line" (reduce max
                                   start
                                   (keep #(some-> (:line (get nodes %))
                                                  long)
                                         (filter #(inside? c %) coords)))
                "kind" (if label "branch" "expression")
                "detail" (if label (str label ": " text) text)})))
         (sort-by #(get % "line"))
         (vec))))

(defn branches
  "The branches of one recorded form that the compiler measured, and the ones that
   ran. A branch counts when it holds an instrumented expression, and it ran when one
   of them ran. A branch that is only a constant has no expression to measure."
  [{:keys [coords hits nodes]}]
  (let [coords
        (set (map str coords))

        hit
        (set (map str hits))

        ran
        (for [[parent {:keys [head size]}]
              nodes

              :when head
              idx
              (range (long (or size 0)))

              :when (branch-label head size idx)
              :let [b
                    (if (= "" parent) (str idx) (str parent "," idx))

                    inner
                    (filter #(or (= b %) (inside? b %)) coords)]
              :when (seq inner)]

          (boolean (some hit inner)))]

    [(count ran) (count (filter true? ran))]))

(defn report
  "The `vis-coverage/1` report of the recorded `forms` that [[measured?]] keeps."
  [forms prefixes]
  {"format" "vis-coverage/1"
   "tool" "clofidence"
   "prefixes" (vec prefixes)
   "forms" (vec
             (for [{:keys [ns file line kind coords hits] :as form}
                   (sort-by (juxt :file :line) (filter measured? forms))

                   :let [coords
                         (set (map str coords))

                         hit
                         (set/intersection coords (set (map str hits)))

                         children
                         (reduce (fn [acc coord]
                                   (if-let [k (child-index coord)]
                                     (update acc
                                             k
                                             (fn [[t h]]
                                               [(inc (long (or t 0)))
                                                (cond-> (long (or h 0))
                                                  (contains? hit coord)
                                                  inc)]))
                                     acc))
                                 (sorted-map)
                                 coords)]]

               {"file" (str file)
                "ns" (str ns)
                "line" (long (or line 0))
                "kind" kind
                "hittable" (count coords)
                "hit" (count hit)
                "children" children
                "branches" (branches form)
                "gaps" (gaps form)}))})

(defn- project-path
  "`file`, classpath-relative, as a path under `root` when a source directory holds it."
  [root file]
  (or (some (fn [^java.io.File dir]
              (let [f (io/file dir file)]
                (when (.isFile f)
                  (str (.relativize (.toPath (.getCanonicalFile (io/file root)))
                                    (.toPath (.getCanonicalFile f)))))))
            (source-dirs root))
      file))

(defn summary
  "Covered and measured expressions, branches and gaps of each file in `report`."
  [root report]
  (->> (get report "forms")
       (group-by #(get % "file"))
       (map (fn [[file forms]]
              {"path" (project-path root file)
               "covered" (reduce + (map #(get % "hit") forms))
               "total" (reduce + (map #(get % "hittable") forms))
               "branches_covered" (reduce + (map #(second (get % "branches" [0 0])) forms))
               "branches_total" (reduce + (map #(first (get % "branches" [0 0])) forms))
               "gaps" (vec (sort-by #(get % "line") (mapcat #(get % "gaps") forms)))}))
       (sort-by #(get % "path"))
       (vec)))

(defn finish!
  "Turn the recorded forms into the report and summarize it. An `error` names a
   run that recorded nothing."
  [root prefixes]
  (let [forms (io/file root forms-path)]
    (if-not (.isFile forms)
      {"unit" "expressions"
       "files" []
       "error" (str "the test run recorded no coverage: the Lazytest hook did not write "
                    forms-path)}
      (let [data (report (edn/read-string (slurp forms)) prefixes)
            out (io/file root report-path)]

        (io/make-parents out)
        (spit out (json/write-json-str data))
        {"unit" "expressions"
         "report" report-path
         "prefixes" (vec prefixes)
         "files" (summary root data)}))))
