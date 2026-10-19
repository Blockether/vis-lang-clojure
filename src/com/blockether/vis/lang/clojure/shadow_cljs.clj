(ns com.blockether.vis.lang.clojure.shadow-cljs
  "shadow-cljs as the ClojureScript TEST RUNNER: which build runs the tests, how
   THIS machine invokes shadow-cljs, and the exact argv that runs a narrowed
   selection. A `*_test.cljs` never loads on the JVM, so `clojure -M:test` can no
   more run it than `node` can run a `.clj` — the build is not a preference here,
   it is the only runtime that exists.

   Three facts decide the command, and each has its own honest refusal instead of
   a guess:

   1. HOW shadow-cljs is installed. `node_modules/.bin/shadow-cljs` (npm) wins
      because it is what the project's own `npm test` runs; a
      `thheller/shadow-cljs` dependency in `deps.edn` runs as
      `clojure -M[:alias] -m shadow.cljs.devtools.cli` — the SAME project may
      carry it either way, and an alias-only dependency needs that alias on the
      command line or the classpath lacks the namespace being `-m`'d. Declared in
      `package.json` but not installed is answered as `npm install`, not as
      \"no ClojureScript runner\".
   2. WHICH build runs tests. Resolve an explicit build or the sole test build;
      never guess between suites. :node-test runs headless, :karma drives its
      own browser, and :browser-test needs a browser RUNTIME that this runner
      cannot supply. Compiling alone is never evidence of passing tests.
   3. WHAT the run is narrowed to. `--config-merge` carries namespace focus and
      disables Node autorun. Compile first, then run Node separately: shadow's
      autorun does not propagate the child exit status. Print overrides with
      `pr-str`; hand-built regexp escapes can make shadow print help and exit
      ZERO without compiling anything."
  (:require [clojure.edn :as edn]
            [clojure.java.io :as io]
            [clojure.string :as str]))

(def ^:private cli-main
  "shadow-cljs' own CLI entry point — every launcher that is not the npm binary
   ends at this main."
  "shadow.cljs.devtools.cli")

(def ^:private shadow-dep
  "The Maven coordinate a deps.edn / project.clj declares shadow-cljs under."
  'thheller/shadow-cljs)

(defn- read-edn
  "Read EDN without evaluating it. Preserve unknown reader tags as opaque data;
   a tag in compiler settings is harmless, but it must never become a literal
   output path or an invented selector value."
  [^java.io.File f]
  (try (when (.isFile f) (edn/read-string {:default tagged-literal} (slurp f)))
       (catch Throwable _ nil)))

(defn- builds-map
  "shadow accepts either a build-id map or a vector of maps carrying :id."
  [builds]
  (if (vector? builds) (into {} (map (juxt :id identity)) builds) builds))

(defn config
  "Read the project's shadow-cljs.edn without loading shadow or evaluating code.
   Normalize its documented map/vector build spellings for both tests and REPLs."
  [root]
  (when-let [cfg (read-edn (io/file (str root) "shadow-cljs.edn"))]
    (update (if (vector? cfg) {:builds cfg} cfg) :builds builds-map)))

(defn- build-layers
  "Only inspect build settings; shadow itself remains responsible for merging and
   compiling them. Development overrides apply to a compile (not release) run."
  [cfg build]
  (let [layers [(:build-defaults cfg) (get-in cfg [:target-defaults (:target build)]) build]]
    (filter map? (concat layers (map :dev layers)))))

(defn- tagged-setting?
  "Whether a setting needs shadow's own reader/effective configuration."
  [value]
  (boolean (some #(instance? clojure.lang.TaggedLiteral %) (tree-seq coll? seq value))))

(defn test-namespace?
  "Does a JS/shared source namespace match a configured test build? This is for
   file discovery, not build selection: multiple matching builds still require
   an explicit build at execution. No filename convention replaces :ns-regexp."
  [cfg requested ns-str]
  (let [builds
        (builds-map (:builds cfg))

        builds
        (if requested
          [(get builds (keyword (str/replace (str requested) #"^:" "")))]
          (vals builds))]

    (boolean
      (some (fn [build]
              (when (#{:node-test :karma :browser-test} (:target build))
                (let [layers
                      (build-layers cfg build)

                      namespaces
                      (mapcat #(when (coll? (:namespaces %)) (:namespaces %)) layers)

                      excluded
                      (set (map str (mapcat #(when (coll? (:exclude %)) (:exclude %)) layers)))

                      pattern
                      (or (last (keep :ns-regexp layers)) "-test$")]

                  (if (seq namespaces)
                    (some #{ns-str} (map str namespaces))
                    (and (not (excluded ns-str))
                         (try (re-find (re-pattern pattern) ns-str) (catch Exception _ false)))))))
            builds))))

(def ^:private launch-alias
  "The tool-owned deps.edn alias that carries shadow-cljs' CLI main. It comes
   last, because tools.deps uses the `:main-opts` of the last alias. A project
   alias with its own `-m shadow.cljs.devtools.cli` then adds only classpath."
  :vis/shadow-cljs-launch)

(defn- classpath-deps
  "The dependency map that `selected` deps.edn aliases give: `:replace-deps`
   replaces the root `:deps`, and `:extra-deps` adds to it."
  [deps selected]
  (let [options
        (map (:aliases deps) selected)

        replacements
        (keep :replace-deps options)]

    (merge (if (seq replacements) (apply merge replacements) (:deps deps))
           (apply merge (keep :extra-deps options)))))

(defn- deps-aliases
  "The deps.edn aliases that a library launch selects, project aliases first.
   An explicit `:deps {:aliases [...]}` in shadow-cljs.edn wins, and `requested`
   (the run_tests aliases) must not add to it. With `:deps true`, or no alias
   list, the requested aliases come first. A unique library alias joins them
   when they do not bring shadow-cljs. Never pick an alias by map order."
  [deps settings requested]
  (let [aliases
        (:aliases deps)

        configured
        (when (and (map? settings) (contains? settings :aliases)) (vec (:aliases settings)))

        requested
        (vec (distinct requested))

        candidates
        (sort-by str
                 (keep (fn [[k v]]
                         (when (or (contains? (:extra-deps v) shadow-dep)
                                   (contains? (:replace-deps v) shadow-dep))
                           k))
                       aliases))

        brings-shadow?
        (fn [selected]
          (contains? (classpath-deps deps selected) shadow-dep))

        fallback
        (cond (contains? (:deps deps) shadow-dep) []
              (= 1 (count candidates)) [(first candidates)]
              :else nil)

        selected
        (cond configured configured
              (and (seq requested) (brings-shadow? requested)) requested
              fallback (vec (distinct (concat requested fallback)))
              :else nil)

        unknown-requested
        (remove #(contains? aliases %) requested)

        extra
        (when configured (remove (set configured) requested))]

    (cond
      (seq unknown-requested) {:error (str "unknown deps.edn aliases: "
                                           (pr-str (vec unknown-requested)))}
      (seq extra)
      {:error (str "shadow-cljs.edn selects :deps {:aliases "
                   (pr-str configured)
                   "}, and run_tests aliases "
                   (pr-str (vec extra))
                   " are not in it — add them there, or drop them from the call; no tests started")}
      (seq (remove #(contains? aliases %) selected))
      {:error (str "shadow-cljs.edn selects unknown deps.edn aliases: "
                   (pr-str (vec (remove #(contains? aliases %) selected))))}
      (and (nil? selected) (seq candidates))
      {:error
       (str
         "multiple shadow-cljs library aliases "
         (pr-str (vec candidates))
         " — pass the one to use in aliases, or select them with :deps {:aliases [...]} in shadow-cljs.edn")}
      (and selected (brings-shadow? selected)) {:aliases selected}
      :else
      {:error
       "the configured deps.edn classpath has no thheller/shadow-cljs dependency; declare it in the selected :deps aliases"})))

(defn- npm-declares-shadow?
  "Whether package.json declares shadow-cljs, for the missing-install hint."
  [^java.io.File f]
  (boolean (try (when (.isFile f) (re-find #"\"shadow-cljs\"\s*:" (slurp f)))
                (catch Throwable _ nil))))

(defn launcher
  "Use the project's npm launcher when installed (it interprets its own config).
   Direct library launches preserve :deps aliases or :lein profile exactly as
   shadow-cljs.edn declares. `requested` deps.edn aliases (the run_tests
   aliases) add classpath to a deps.edn launch. A Lein launch refuses them.
   Vis never installs or depends on shadow-cljs."
  ([root] (launcher root []))
  ([root requested]
   (let [dir
         (io/file (str root))

         bin
         (io/file dir "node_modules" ".bin" "shadow-cljs")

         cfg
         (config root)

         lein
         (:lein cfg)

         deps
         (read-edn (io/file dir "deps.edn"))

         requested
         (vec (distinct (map (fn [a]
                               (keyword (str/replace (str a) #"^:+" "")))
                             requested)))

         configured
         (when (map? (:deps cfg)) (:aliases (:deps cfg)))

         alias-arg
         (fn [selected]
           (apply str (map str selected)))]

     (cond
       (and (seq requested) lein)
       {:error
        (str "aliases "
             (pr-str requested)
             " are deps.edn aliases, but shadow-cljs.edn launches through :lein; no tests started")}
       (.isFile bin)
       (cond
         (empty? requested) {:kind :npm :argv [(.getPath bin)]}
         (not (:deps cfg))
         {:error
          (str
            "aliases "
            (pr-str requested)
            " are deps.edn aliases, but shadow-cljs.edn has no :deps, so its build does not use deps.edn; no tests started")}
         (and configured (seq (remove (set configured) requested)))
         {:error (str
                   "shadow-cljs.edn selects :deps {:aliases "
                   (pr-str (vec configured))
                   "}, and run_tests aliases "
                   (pr-str (vec (remove (set configured) requested)))
                   " are not in it — add them there, or drop them from the call; no tests started")}
         :else {:kind :npm :argv [(.getPath bin) "--aliases" (alias-arg requested)]})
       lein {:kind :lein
             :argv (into ["lein"]
                         (concat (when-let [profile (:profile lein)]
                                   ["with-profile" (str profile)])
                                 ["run" "-m" cli-main]))}
       (or (:deps cfg)
           (contains? (:deps deps) shadow-dep)
           (some (fn [[_ opts]]
                   (or (contains? (:extra-deps opts) shadow-dep)
                       (contains? (:replace-deps opts) shadow-dep)))
                 (:aliases deps)))
       (let [{:keys [aliases error]} (deps-aliases deps (:deps cfg) requested)]
         (if error
           {:error error}
           {:kind :deps
            :argv ["clojure" "-Sdeps"
                   (binding [*print-namespace-maps* false]
                     (pr-str {:aliases {launch-alias {:main-opts ["-m" cli-main]}}}))
                   (str "-M" (alias-arg aliases) launch-alias)]}))
       (npm-declares-shadow? (io/file dir "package.json"))
       {:error (str
                 "shadow-cljs is declared in package.json but not installed — run `npm install` in "
                 (.getPath dir)
                 " (node_modules/.bin/shadow-cljs is missing)")}
       :else
       {:error
        (str
          "no way to run shadow-cljs in "
          (.getPath dir)
          " — install the project's npm dependencies, or configure its :deps/:lein library launcher")}))))

(def ^:private target-kind
  "What each shadow-cljs TEST target needs to produce a result: `:headless` runs
   itself under node, `:karma` drives its own browser through the karma binary,
   and `:runtime` needs a browser to connect back to the build — compiling that
   one asserts NOTHING, which is why it is refused instead of reported green."
  {:node-test :headless :karma :karma :browser-test :runtime})

(defn- describe-builds
  "Every build in the config as `id (:target)`, id-sorted — what a refusal owes
   the caller: the choices it actually had."
  [builds]
  (if (seq builds)
    (str/join ", "
              (map (fn [[id build]]
                     (str id " (" (pr-str (:target build)) ")"))
                   (sort-by (comp str key) builds)))
    "none"))

(defn test-build
  "Resolve an explicit test build or the sole test target. Different builds may
   use different runtimes, dependencies and filters; alphabetical order is not
   evidence that one of them is the requested suite."
  [cfg requested]
  (let [builds
        (builds-map (:builds cfg))

        candidates
        (filter (fn [[_ build]]
                  (target-kind (:target build)))
                builds)

        id
        (when (seq (str requested)) (keyword (str/replace (str requested) #"^:" "")))

        [id build]
        (if id [id (get builds id)] (when (= 1 (count candidates)) (first candidates)))]

    (cond
      (and (seq (str requested)) (nil? build)) {:error (str "shadow-cljs.edn has no build " id
                                                            " — builds: " (describe-builds builds))}
      (and build (nil? (target-kind (:target build))))
      {:error (str
                "build "
                id
                " targets "
                (pr-str (:target build))
                ", which runs no tests — a test build targets :node-test, :karma or :browser-test")}
      build {:id id :target (:target build) :build build}
      (seq candidates) {:error (str "multiple shadow-cljs test builds — specify build: "
                                    (describe-builds candidates))}
      :else
      {:error
       (str
         "shadow-cljs.edn declares no test build — builds: "
         (describe-builds builds)
         ". Add one, e.g. :builds {:test {:target :node-test :output-to \"target/node-tests.js\"}}")})))

(defn ns-regexp
  "The `:ns-regexp` that selects EXACTLY `nses`. shadow-cljs `re-find`s this
   against every compiled namespace, so the anchors are what stop `a.core-test`
   from also dragging in `a.core-test-helpers`."
  [nses]
  (str "^("
       (str/join "|"
                 (map (fn [n]
                        (str/replace (str n)
                                     #"[\\.\^$|?*+(){}\[\]]"
                                     (fn [ch]
                                       (str "\\" ch))))
                      (sort nses)))
       ")$"))

(defn- external-config?
  "Global/env overlays can change :dev output paths and selectors. Until shadow
   exposes effective config, refuse to execute an assumed build."
  []
  (or (not (str/blank? (System/getenv "SHADOW_CLJS")))
      (some (fn [^java.io.File f]
              (.isFile f))
            (concat (for [key
                          ["XDG_CONFIG_HOME" "LOCALAPPDATA"]

                          :let [dir
                                (System/getenv key)]
                          :when (seq dir)]

                      (io/file dir "shadow-cljs" "config.edn"))
                    [(io/file (System/getProperty "user.home") ".shadow-cljs" "config.edn")]))))

(defn run-steps
  "The commands that RUN the ClojureScript tests of `root`, in order, as
   `{:build :target :kind :steps [{:argv [...]}]}` — or `{:error ...}` when this
   project cannot run them, which is data the caller reports, never an exception.
   `:nses` narrows the run; an empty selection leaves the configured suite untouched. `:build`
   is required when more than one test build is configured.
   `:node-test` disables autorun, verifies compilation, then runs Node separately
   to retain its exit status. The caller supplies a unique `:output-root` for
   Node artifacts (including :dev overrides) and owns its cleanup; no project
   config is written. :karma keeps its configured output and single-shot binary."
  [root {:keys [nses build output-root aliases]}]
  (let [cfg (config root)]
    (if (nil? cfg)
      {:error (str "no shadow-cljs.edn in "
                   root
                   " — ClojureScript tests (*_test.cljs) run through a shadow-cljs build")}
      (if (external-config?)
        {:error
         "external shadow-cljs configuration (SHADOW_CLJS or user config) requires effective-config resolution; no tests started"}
        (let [{:keys [error id target] selected-build :build} (test-build cfg build)
              layers (build-layers cfg selected-build)
              fixed-nses (set (map str
                                   (mapcat #(when (coll? (:namespaces %)) (:namespaces %)) layers)))
              excluded (set (map str (mapcat #(when (coll? (:exclude %)) (:exclude %)) layers)))
              output-to (last (keep :output-to layers))
              {launcher-error :error argv :argv kind :kind} (launcher root aliases)]

          (cond
            error {:error error}
            (some tagged-setting? (map #(select-keys % [:namespaces :exclude :ns-regexp]) layers))
            {:error
             "dynamic test selectors require shadow's effective configuration; no tests started"}
            (and (= :node-test target) (or (not (string? output-to)) (str/blank? output-to)))
            {:error
             "node-test needs a literal :output-to path (including :dev/build defaults); dynamic output paths are not supported"}
            (and (seq nses) (seq fixed-nses) (not= (set nses) fixed-nses))
            {:error (str
                      "build " id
                      " has explicit :namespaces; shadow merges those vectors"
                      " additively and they override :ns-regexp, so this focus cannot be honored")}
            (and (seq nses) (empty? fixed-nses) (some excluded nses))
            {:error (str "build " id " excludes requested namespaces; no tests started")}
            ;; Refused BEFORE the launcher is resolved: installing shadow-cljs
            ;; would not make a browser build runnable, so `npm install` is the
            ;; wrong thing to be told first.
            (= :runtime (target-kind target))
            {:error (str "build "
                         id
                         " targets "
                         (pr-str target)
                         ": its tests run INSIDE a browser that connects back to the build,"
                         " and run_tests has no browser to connect."
                         " Run them with `shadow-cljs watch "
                         (subs (str id) 1)
                         "` and an open page,"
                         " or add a headless test build (:node-test, or :karma with"
                         " node_modules/.bin/karma installed)")}
            launcher-error {:error launcher-error}
            :else (let [merged (cond-> {}
                                 (= :node-test target)
                                 (merge (cond-> {:autorun false}
                                          output-root
                                          (assoc :output-to
                                            (str (io/file output-root "tests.js")) :output-dir
                                            (str (io/file output-root "js")))))

                                 (seq nses)
                                 (assoc :ns-regexp (ns-regexp nses)))
                        ;; :dev overrides may otherwise undo autorun/focus. Do not
                        ;; duplicate or interpret the rest of shadow's compiler config.
                        merged (cond-> merged
                                 (some :dev (build-layers cfg selected-build))
                                 (assoc :dev merged))
                        compile-argv (into (vec argv)
                                           (cond-> ["compile" (subs (str id) 1)]
                                             (seq merged)
                                             (into ["--config-merge" (pr-str merged)])))
                        base {:build (subs (str id) 1) :target target :kind kind}]

                    (if (= :karma (target-kind target))
                      (let [karma (io/file (str root) "node_modules" ".bin" "karma")]
                        (if (.isFile karma)
                          (assoc base
                            :steps [{:argv compile-argv :compile? true}
                                    {:argv [(.getPath karma) "start" "--single-run"]}])
                          {:error (str "build "
                                       id
                                       " targets :karma but node_modules/.bin/karma is missing"
                                       " — run `npm install` in "
                                       root)}))
                      (assoc base
                        :steps [{:argv compile-argv :compile? true}
                                {:argv ["node"
                                        (if output-root
                                          (str (io/file output-root "tests.js"))
                                          output-to)]}])))))))))
