(ns com.blockether.vis.lang.clojure.api
  "The Clojure tools as plain functions: format, lint, REPL lifecycle and REPL eval.

   Each one takes the call context `env` — `{:workspace/root <dir> :session-id <id>}` —
   plus the call's own argument, and answers the `{:result :success? :error}` envelope
   `cli` serializes. `format` runs the ADD-ONLY delimiter repair (`repair/repair-source`)
   before the formatter: a delimiter you omitted is added back, one you WROTE is never
   deleted — a lost opening `(` and one `)` too many are the same string, so deleting is
   a guess that rewrites code."
  (:require [clojure.edn :as edn]
            [clojure.java.io :as io]
            [clojure.string :as str]
            [com.blockether.vis.lang.clojure.format :as fmt]
            [com.blockether.vis.lang.clojure.host :as host]
            [com.blockether.vis.lang.clojure.lint :as lint]
            [com.blockether.vis.lang.clojure.reflection :as reflection]
            [com.blockether.vis.lang.clojure.repair :as repair]
            [com.blockether.vis.lang.clojure.repl-manager :as repl-manager]))

;; Tool fns

(defn- env-root
  ^String [env]
  (or (:workspace/root env)
      (throw (ex-info "clj/* tool fired without :workspace/root in env"
                      {:type :clj/no-workspace}))))

(defn- expand-home
  "Expand a leading `~` / `~/…` to the user's home dir (`user.home`), so a REPL
   `cwd` written the way a human types it resolves to a real absolute path
   instead of a bogus `~` segment under the workspace root. `~user` (another
   user's home) is NOT resolved — it passes through untouched."
  ^String [^String d]
  (let [home (System/getProperty "user.home")]
    (cond (= "~" d) (or home d)
          (str/starts-with? d "~/") (if home (str home (subs d 1)) d)
          :else d)))

(defn- resolve-repl-dir
  "Resolve a `:start`/`:status`/`:stop` target dir against the workspace `root`.
   A blank dir means the workspace root; a leading `~`/`~/…` expands to the
   user's home dir; a relative dir is taken under root; an absolute dir is used
   as-is. Returns a canonical path string — the SAME value for a given target no
   matter how it was spelled, so start, stop, and eval-by-id all agree on one id."
  ^String [root dir]
  (let [d
        (expand-home (str dir))

        f
        (cond (= "" d) (io/file root)
              (.isAbsolute (io/file d)) (io/file d)
              :else (io/file root d))]

    (.getCanonicalPath f)))

(defn- coerce-aliases
  "Accept [\"dev\" \"test\"], \"dev\", [:dev], or nil → a vec of alias name
   STRINGS or nil. No keyword minting: aliases stay strings end-to-end (deps.edn
   alias suffix, resource detail that crosses the strings-only boundary)."
  [a]
  (cond (nil? a) nil
        (sequential? a) (mapv name a)
        :else [(name a)]))

(defn repl-start-fn
  "Manage THIS session's workspace nREPL(s). The `clj` extension tools
   `repl_start` / `repl_status` / `repl_stop` / `repl_connect` reach this
   library through the `repl` verb as an op STRING (default \"status\") +
   optional opts dict `{\"cwd\": <path>, \"aliases\": [\"dev\", \"test\"]}`:

     \"status\"  — managed-process view for this session (always allowed)
     \"start\"   — start a project nREPL subprocess (always allowed)
     \"stop\"    — stop a Vis-managed nREPL / DETACH an external one (always allowed)
     \"connect\" — attach to an EXTERNAL user-started nREPL: opts {\"port\": N,
                 \"host\"?: S (default localhost), \"build\"?: S}; vis never
                 spawns/kills it. \"build\" names a shadow-cljs build and makes it
                 a ClojureScript REPL: that build is selected in the session every
                 later repl_eval reuses, so the eval lands in its JS runtime. With
                 a \"build\" and no \"port\", the port is read from the project's own
                 .shadow-cljs/nrepl.port. An attachment is INDEPENDENT of the
                 managed REPL for the same \"cwd\" — both live at once, each under
                 its own id (`nrepl:~/proj` and `nrepl:~/proj#app`), so a
                 ClojureScript attach never costs you the JVM REPL.

   \"cwd\" runs the REPL in a subdir (e.g. an extension) instead of the workspace
   root — that's how MULTIPLE REPLs coexist, each addressed by its id. \"aliases\"
   default to [:dev :test] (full deps/paths, user :main-opts dropped). Live nREPL
   state already rides in ctx under `:session/env :languages :clojure :nrepl`;
   this tool acts on it."
  ([env] (repl-start-fn env "status" nil))
  ([env op] (repl-start-fn env op nil))
  ([env op opts]
   (let [root
         (env-root env)

         sid
         (:session-id env)

         ;; Positional op arrives as a STRING from the model (strings-only
         ;; boundary); dispatch on it directly, no keyword minting. Default
         ;; "status".
         op
         (if (string? op) op "status")

         opts
         (when (map? opts) opts)

         dir
         (resolve-repl-dir root (get opts "cwd"))

         aliases
         (coerce-aliases (get opts "aliases"))

         ;; THIS start's own environment, over the project's. An ARGUMENT of the
         ;; call, so the record of the call says what the REPL was started with.
         repl-env
         (get opts "env")]

     (case op
       "status"
       (host/success {:result (repl-manager/status sid dir)})

       "connect"
       (let [port
             (get opts "port")

             host
             (get opts "host")

             build
             (get opts "build")]

         (when-not (or port build)
           (throw (ex-info (str "repl_connect needs {\"port\": <the external nREPL's port>}"
                                " — or {\"build\": \"app\"} to attach to the shadow-cljs watch"
                                " running under \"cwd\" (it publishes its own port)."
                                " Optional \"host\", \"cwd\" — e.g."
                                " clj.repl_connect(port=7888) /" " clj.repl_connect(build=\"app\")")
                           {:type :clj/bad-args :got opts})))
         (let [r (repl-manager/connect!
                   sid
                   dir
                   {:host host
                    :port (when port
                            (if (string? port) (Long/parseLong (str/trim port)) (long port)))
                    :build build})]
           (host/success {:result r})))

       "stop"
       ;; The result names WHICH repl went — a dir can hold both a managed REPL
       ;; and an attachment of its own.
       (let [r (if (get opts "build") (repl-manager/detach! sid dir) (repl-manager/stop! sid dir))]
         (host/success {:result r}))

       "start"
       (do (when-not (.isDirectory (io/file dir))
             (throw (ex-info (str "repl_start target cwd does not exist: "
                                  (repl-manager/home-relativize (str dir)))
                             {:type :clj/bad-args :dir dir})))
           ;; No "restart": start! REUSES a healthy REPL ("already-running") and
           ;; a REPL you actually want replaced is stopped explicitly first, so a
           ;; hung relaunch can never leave the caller with nothing.
           (let [result (repl-manager/start! sid dir {:aliases aliases :env repl-env})]
             (host/success {:result result})))

       (throw (ex-info (str "clojure REPL lifecycle: unknown op " (pr-str op))
                       {:type :clj/bad-args
                        :got op
                        :examples
                        ["clj.repl_start()" "clj.repl_status()"
                         "clj.repl_start(cwd=\"apps/vis-tui\", aliases=[\"dev\", \"test\"])"
                         "clj.repl_stop()"]}))))))

(defn- coerce-eval-arg
  "Accept the call shapes a caller is most likely to send:
     \"(+ 1 1)\"
     {\"code\": \"(+ 1 1)\"}
     {\"code\": \"...\", \"port\": 7888, \"ns\": \"user\", \"timeout_ms\": 5000}
     {\"code\": \"...\", \"id\": \"<repl-id>\"}   ; target a registered REPL"
  [arg]
  (cond (string? arg) {"code" arg}
        (map? arg) arg
        :else (throw (ex-info "repl-eval expects a code string or opts map"
                              {:type :clj/bad-args
                               :got arg
                               :examples
                               ["clj.repl_eval(\"(+ 1 1)\")"
                                "clj.repl_eval(\"...\", ns=\"user\", timeout_ms=5000)"]}))))

(defn- strip-blank-repl-fields
  "Prune result fields the model gains nothing from seeing: nil, blank strings,
   empty collections, and the pr-str of nil (a bare `\"nil\"` value, or a `values`
   vector that is only nils). Keeps every informative field — a real value,
   captured stdout/stderr, errors, status, ns and timing — so a `(println …)`
   run surfaces its STDOUT WITHOUT a redundant `\"value\": \"nil\"`, and a plain
   `(+ 1 2)` shows only its value, no empty `out`/`err`/`ex`/`root_ex` noise.
   Presentation-only: the UI op-card and the internal `eval!` callers still see
   the full nREPL shape; this trims just the map that crosses to the model."
  [m]
  (into {}
        (remove (fn [[_ v]]
                  (or (nil? v)
                      (= "nil" v)
                      (and (string? v) (str/blank? v))
                      (and (coll? v) (or (empty? v) (every? #(= "nil" %) v))))))
        m))

(defn clj-eval-fn
  "Evaluate Clojure over a RUNNING nREPL in this session. Target resolution:
     - explicit `port` → dial it directly (escape hatch);
     - `id`/`repl_id`  → the REPL registered under that id in THIS session;
     - `cwd`           → the REPL rooted at that directory (when the session owns one);
     - no id, 1 REPL   → use it (the implicit default);
     - no id, >1 REPLs → the REPL owning `cwd` (default: the workspace root) when
                         present, else the first (dir-sorted);
     - no id, 0 REPLs  → error (:clj/no-repl): no running nREPL to hit.
   A connect failure surfaces as DATA so the model can repl / wait."
  ([env arg]
   (let [m
         (coerce-eval-arg arg)

         code
         (get m "code")

         port
         (get m "port")

         host
         (or (get m "host") "localhost")

         ns
         (get m "ns")

         timeout_ms
         (get m "timeout_ms")

         root
         (env-root env)

         sid
         (:session-id env)

         requested-dir?
         (contains? m "cwd")

         requested-rid
         (some-> (or (get m "id") (get m "repl_id"))
                 str
                 str/trim
                 not-empty)

         rid
         ;; A model may carry a stale/previous ctx resource id while also passing
         ;; an explicit `cwd`. If that id is not live in THIS session, let `cwd`
         ;; drive the default resolution instead of failing on the unknown id.
         ;; With no explicit cwd, keep the strict id contract and surface the error.
         (when-not (and requested-dir?
                        requested-rid
                        (not (repl-manager/repl-by-id sid requested-rid)))
           requested-rid)

         default-dir
         (resolve-repl-dir root (get m "cwd"))

         run
         (fn [target repl-label]
           ;; Carry the evaluated FORM back on the result (string key, crosses the
           ;; strings-only boundary) so the repl_eval op-card can show it in the
           ;; collapsed chip / expanded FORM section. `repl` names WHICH nREPL
           ;; actually ran it, so a multi-REPL session reports the target used.
           ;; The eval goes through repl-manager, not straight to the client: a
           ;; target attached to a shadow-cljs BUILD has to have that build selected
           ;; in the nREPL session first, or the same code answers as JVM Clojure.
           (-> (repl-manager/eval!
                 (assoc target :host (or (:host target) host))
                 {:code code :ns ns :pretty? true :timeout-ms (or timeout_ms 30000)})
               strip-blank-repl-fields
               (assoc "code" code
                      "repl" repl-label)))]

     (if port
       ;; Explicit port: the escape hatch — dial exactly what was asked.
       (host/success {:result (run {:host host :port port} (str host ":" port))})
       ;; Resolve a RUNNING REPL. A missing/unknown REPL is an EXPECTED,
       ;; actionable condition — catch it and return a TIGHT failure envelope
       ;; so the model sees just the one-line message + hint, NOT the raw
       ;; `clojure.lang.ExceptionInfo` class + `{:type … :dir …}` ex-data and
       ;; the internal nREPL/Compiler stack trace `ex->op-error` would attach.
       (try
         (let [target (repl-manager/resolve-target! sid rid default-dir)]
           ;; An EXTERNAL attachment may live on a non-localhost host — dial ITS
           ;; host, not the caller's default.
           (host/success {:result (run target (:id target))}))
         (catch clojure.lang.ExceptionInfo e
           (case (:type (ex-data e))
             :clj/no-repl
             (host/failure {:error {:message (str "no REPL running in "
                                                  ;; Home-homogenized: the message reads
                                                  ;; `~/vis`, matching the REPL ids in
                                                  ;; session["resources"] — never a raw
                                                  ;; `/Users/you/vis`.
                                                  (repl-manager/home-relativize (str (:dir (ex-data
                                                                                             e))))
                                                  " — start one: clj.repl_start()")
                                    :hint "then retry the eval"}})

             :clj/unknown-repl-id
             (host/failure {:error {:message (str "no REPL under id '"
                                                  (:id (ex-data e))
                                                  "' — check clj.repl_status()")
                                    :hint "pass a live id, or omit it to use the default REPL"}})

             (throw e))))))))

(defn clj-repair+format
  "The combined Clojure tidy behind `format`: the ADD-ONLY delimiter repair
   (`repair/repair-source`) FIRST, THEN indentation via the config-driven formatter
   (`fmt/format-source` picks zprint when a `.zprint.edn`/`.zprintrc` is near `path`,
   else cljfmt). Total — returns `code` unchanged on any failure of either step, and
   leaves source whose repair was refused exactly as it was written."
  ([code] (clj-repair+format code nil))
  ([code path] (fmt/format-source (:code (repair/repair-source code)) path)))

(defn- relativize-path
  "Rewrite an absolute path to one relative to workspace `root` so tool output
   reads `src/foo.clj` instead of the noisy machine-absolute `/Users/…/src/foo.clj`.
   Paths outside root (and the root itself) collapse their user-home prefix to
   `~`; non-path sentinels like `<stdin>` pass through unchanged."
  [^java.io.File root file]
  (let [s (str file)]
    (if (and root (seq s) (not= s "<stdin>"))
      (try (let [rp (.toPath (.getCanonicalFile root))
                 fp (.toPath (.getCanonicalFile (io/file s)))
                 rel (when (.startsWith fp rp) (str (.relativize rp fp)))]

             ;; Under root -> `src/foo.clj`. Otherwise (outside root, or the root
             ;; itself, whose relativization is "") fall back to a home-homogenized
             ;; absolute path so output reads `~/vis` — never a raw `/Users/you/…`.
             (if (seq rel) rel (repl-manager/home-relativize (str fp))))
           (catch Throwable _ (repl-manager/home-relativize s)))
      s)))

;; Only true Clojure SOURCE dialects — everything the repo's canonical formatter
;; touches. Deliberately NOT `.edn`: zprint sorts map keys + reflows, which would
;; churn hand-ordered config files (deps.edn, `.zprint.edn`) that are kept by
;; hand, making the format-on-write hook fight their authors.
;; A babashka `.bb` script counts: it is Clojure source carrying a shebang the
;; reader and zprint both read straight through as a comment.
(def ^:private clj-source-exts [".clj" ".cljs" ".cljc" ".cljx" ".bb"])

(defn- clj-source-file?
  "True when `path` names a Clojure source file (by extension)."
  [path]
  (let [p (str/lower-case (str path))]
    (boolean (some #(str/ends-with? p %) clj-source-exts))))

(def ^:private denied-dir-names
  "Directory names we NEVER format or lint: build artifacts, vendored deps and
   tool caches. Even when a caller points a recursive walk straight at one (or a
   real source path happens to contain one), everything under such a dir is
   skipped."
  #{"target" "dist" "build" "out" "classes" ".cpcache" ".gradle" "node_modules" ".shadow-cljs"
    ".cljs_node_repl" ".clj-kondo" ".clojure-lsp" ".lsp" ".calva" ".git" ".hg" ".svn" ".bzr" ".idea"
    ".vscode"})

(defn- under-denied-dir?
  "True when any ancestor directory of `f` is in `denied-dir-names`."
  [^java.io.File f]
  (loop [p (.getParentFile f)]
    (cond (nil? p) false
          (contains? denied-dir-names (.getName p)) true
          :else (recur (.getParentFile p)))))

(defn- expand-clj-source-files
  "Expand `paths` (resolved against workspace `root` when relative) into concrete
   Clojure source files. A DIRECTORY is walked RECURSIVELY, collecting every
   `.clj`/`.cljs`/`.cljc`/`.cljx`/`.bb` file under it; a plain file is kept
   as-is; a non-existent path is dropped. Returns a de-duplicated, sorted vector
   of absolute path strings."
  [^java.io.File root paths]
  (->> paths
       (mapcat (fn [p]
                 (let [g
                       (io/file (str p))

                       f
                       (if (.isAbsolute g) g (io/file root (str p)))]

                   (cond (.isDirectory f) (->> (file-seq f)
                                               (filter #(.isFile ^java.io.File %))
                                               (filter #(clj-source-file? (str %)))
                                               (remove under-denied-dir?))
                         (.isFile f) [f]
                         :else nil))))
       (map str)
       (distinct)
       (sort)
       (vec)))

(defn- read-edn-safe
  "Read `f` as EDN, returning nil on any failure (missing / malformed)."
  [f]
  (try (edn/read-string (slurp (str f))) (catch Throwable _ nil)))

(defn- deps-source-paths
  "Source/test roots declared by a parsed deps.edn map (relative to its dir):
   its `:paths` plus every alias `:extra-paths`."
  [deps]
  (when (map? deps) (concat (:paths deps) (mapcat :extra-paths (vals (:aliases deps))))))

(defn- deps-local-roots
  "Every `:local/root` module dir declared by a parsed deps.edn map: from its
   `:deps` plus every alias `:extra-deps`."
  [deps]
  (when (map? deps)
    (->> (concat (vals (:deps deps)) (mapcat (comp vals :extra-deps) (vals (:aliases deps))))
         (keep #(when (map? %) (:local/root %))))))

(defn- discover-project-source-paths
  "Best-effort discovery of the project's OWN Clojure source roots, driven by the
   root `deps.edn` (option B — self-maintaining allowlist, not a hardcoded layout):
     - the root module's `:paths` + every alias `:extra-paths`
     - every `:local/root` module reachable TRANSITIVELY (a local dep's own
       deps.edn may point at further locals — we follow them, guarding against
       cycles / repeats), using each module's declared paths (or `src`+`test`).
   Returns existing directories as de-duplicated, sorted absolute strings. This
   naturally excludes vendored code and test fixtures (nothing points at them).
   Falls back to `src`+`test`, then the workspace root, when no deps.edn is found."
  [^java.io.File root]
  (let [root-deps
        (read-edn-safe (io/file root "deps.edn"))

        modules
        (loop [queue
               (map #(io/file root (str %)) (deps-local-roots root-deps))

               seen
               #{}

               acc
               []]

          (if-let [dir (first queue)]
            (let [canon (try (.getCanonicalPath ^java.io.File dir) (catch Throwable _ (str dir)))]
              (if (contains? seen canon)
                (recur (rest queue) seen acc)
                (let [md (read-edn-safe (io/file dir "deps.edn"))
                      subs (map #(io/file dir (str %)) (deps-local-roots md))]

                  (recur (concat (rest queue) subs) (conj seen canon) (conj acc [dir md])))))
            acc))

        candidates
        (concat (map #(io/file root (str %)) (deps-source-paths root-deps))
                (mapcat (fn [[dir md]]
                          (map #(io/file dir (str %))
                               (or (seq (deps-source-paths md)) ["src" "test"])))
                        modules))

        dirs
        (->> candidates
             (filter #(.isDirectory ^java.io.File %))
             (map #(try (.getCanonicalPath ^java.io.File %) (catch Throwable _ (str %))))
             (distinct)
             (sort)
             (vec))]

    (cond (seq dirs) dirs
          :else (let [d (->> ["src" "test"]
                             (map #(io/file root %))
                             (filter #(.exists ^java.io.File %))
                             (mapv str))]
                  (if (seq d) d [(str root)])))))

(defn- clj-format-one-file!
  "Format a single file at `path` IN PLACE (add-only paren repair + cljfmt), writing
   back ONLY when the content changes. Returns a per-file result map with the
   workspace-relative path.

   Runs the repair ONCE and reuses its verdict for the `\"repaired\"` flag, for the
   `\"repairs\"` notes naming the lines it completed, and for `\"unbalanced\"` — a repair
   existed but would have DELETED a delimiter this file already has, so the file is
   left exactly as it is and the reason is reported instead."
  [env path]
  (let [code
        (slurp (str path))

        for-path
        (or path (:workspace/root env))

        {:keys [repaired? repairs why] fixed :code}
        (repair/repair-source code)

        out
        (fmt/format-source fixed for-path)]

    (when (not= out code) (spit (str path) out))
    (cond-> {"path" (relativize-path (io/file (or (:workspace/root env) ".")) path)
             "changed" (not= out code)
             "repaired" repaired?
             "wrote" (not= out code)
             "formatter" (name (fmt/formatter-for for-path))}
      (seq repairs)
      (assoc "repairs" repairs)

      why
      (assoc "unbalanced" why))))

(defn clj-format-fn
  "Format Clojure source for the `format` verb (`clj.format_code`). Accepts:
     - a raw code string / {\"code\": ...}   -> the formatted \"text\" + changed? + char delta
     - {\"path\": \"src/foo.clj\"}              -> format that file IN PLACE
     - {\"paths\": [\"src\" \"test\" ...]}        -> format those paths IN PLACE; a
         DIRECTORY is walked RECURSIVELY (every .clj/.cljs/.cljc/.cljx/.bb under it)
     - nothing / {}                         -> format the whole project's source
         roots (every deps.edn module's :paths + test), skipping build/vendor
         dirs (target, dist, node_modules, .clj-kondo, .clojure-lsp, .cpcache…)
   Paths are resolved against the workspace root when relative. Every result
   NAMES the backend that ran: `\"formatter\"` (\"zprint\" | \"cljfmt\") on a
   single file / code string, and the distinct `\"formatters\"` set on a batch."
  ([arg] (clj-format-fn nil arg))
  ([env arg]
   (let [root
         (io/file (or (:workspace/root env) "."))

         paths
         (when (map? arg) (get arg "paths"))

         path
         (when-let [p (and (map? arg)
                           (let [p (get arg "path")]
                             (when-not (str/blank? (str p)) p)))]
           (let [f (io/file (str p))]
             (str (if (.isAbsolute f) f (io/file root (str p))))))

         has-code?
         ;; A blank `"code": ""` default must not shadow a real `path`/`paths`
         ;; (otherwise we'd format an empty snippet instead of the file).
         (and (map? arg) (not (str/blank? (str (get arg "code")))))

         default?
         (or (nil? arg) (and (map? arg) (not (seq paths)) (not path) (not has-code?)))

         batch
         (cond (seq paths) (expand-clj-source-files root paths)
               default? (expand-clj-source-files root (discover-project-source-paths root)))]

     (if batch
       (let [files (mapv #(clj-format-one-file! env %) batch)]
         (host/success {:result {"op" "clj-format"
                                 "files" files
                                 "changed" (count (filter #(get % "changed") files))
                                 "formatters" (vec (sort (distinct (keep #(get % "formatter")
                                                                         files))))}}))
       (let
         [code
          (cond
            (string? arg) arg
            has-code? (str (get arg "code"))
            path (slurp (str path))
            :else
            (throw
              (ex-info
                "format expects a code string, {\"code\": ...}, {\"path\": ...}, {\"paths\": [...]}, or {} for the whole project"
                {:type :clj/bad-args
                 :got arg
                 :examples ["clj.format_code(source=\"(defn f [x]\\n(* x 2))\")"
                            "clj.format_code(source=\"...\")" "clj.format_code([\"src/foo.clj\"])"
                            "clj.format_code([\"src\", \"test\"])" "clj.format_code()"]})))

          for-path
          (or path (:workspace/root env))

          {:keys [repaired? repairs why] fixed :code}
          (repair/repair-source code)

          out
          (fmt/format-source fixed for-path)]

         (when (and path (not= out code)) (spit (str path) out))
         (host/success {:result (cond-> {"op" "clj-format"
                                         "changed" (not= out code)
                                         "chars" (- (count out) (count code))
                                         "repaired" repaired?
                                         "formatter" (name (fmt/formatter-for for-path))}
                                  (not path)
                                  (assoc "text" out)

                                  (seq repairs)
                                  (assoc "repairs" repairs)

                                  why
                                  (assoc "unbalanced" why)

                                  path
                                  (assoc "path"
                                    (relativize-path (io/file (or (:workspace/root env) ".")) path)
                                    "wrote"
                                    (not= out code)))}))))))

(defn- nearest-kondo-dir
  "The nearest `.clj-kondo` config directory walking UP from `file`, or nil when
   none exists above it. Mirrors format's per-file `.zprint.edn` walk so a NESTED
   project's `.clj-kondo` wins over the workspace root's: clj-kondo's own `run!`
   otherwise resolves config from the process CWD (`user.dir`) and never sees a
   nested config dir. nil means 'no project config' -> clj-kondo default."
  ^java.io.File [^java.io.File file]
  (loop [dir (if (.isDirectory file) file (.getParentFile file))]
    (when dir
      (let [c (io/file dir ".clj-kondo")]
        (if (.isDirectory c) c (recur (.getParentFile dir)))))))

(defn- lint-grouped
  "Lint absolute source-`files`, GROUPED by each file's nearest `.clj-kondo` dir,
   running clj-kondo once per group under that group's `:config-dir` — so files
   in a nested project are linted against ITS config, not the workspace root's.
   Files with no `.clj-kondo` above them fall back to clj-kondo's default
   resolution (key nil). The per-group results are merged into one uniform map."
  [files]
  (if (empty? files)
    lint/empty-result
    (->> files
         (group-by #(nearest-kondo-dir (io/file (str %))))
         (mapv (fn [[cfg-dir group]]
                 (lint/lint-paths group cfg-dir)))
         (lint/merge-results))))

(defn clj-lint-fn
  "clj-kondo lint for the `lint` verb (`clj.lint_code`). Accepts:
     - a raw code string / {\"code\": ...}  -> lint it on stdin
     - {\"path\": \"src/foo.clj\"}           -> lint that file
     - {\"paths\": [\"src\", \"test\"]}        -> lint those paths
     - nothing / {}                       -> lint the whole project's source roots
         (every deps.edn module's :paths + test), skipping build/vendor dirs
   `path` and `paths` are UNIONED (not shadowing); a target that resolves to
   nothing is an ERROR, not a silent `clean`.
   Paths are resolved against the workspace root when relative. Finding \"file\"
   paths are reported RELATIVE to the workspace root (absolute only when outside).

   Findings come from one or more PROVIDERS, tagged per finding as `\"provider\"`
   and listed under `\"providers\"`: `\"clj-kondo\"` (static analysis, every
   branch) and `\"general\"` (the compiler's reflection + boxed-math warnings).
   Reflection/boxed-math only exist at compile time, so `\"general\"` COMPILES its
   target: the code-string snippet, or every source file the lint targets (path /
   paths / whole project) — each in a throwaway namespace that is torn down."
  [env arg]
  (let [root
        (io/file (or (:workspace/root env) "."))

        path
        (when (map? arg)
          (let [p (get arg "path")]
            (when-not (str/blank? (str p)) p)))

        paths
        (when (map? arg) (get arg "paths"))

        code
        ;; Models routinely emit EVERY schema key with an empty default
        ;; (`"code": ""` alongside a real `"path"`); a blank `code` must NOT
        ;; shadow the path (that would lint an empty snippet and falsely
        ;; report `snippet — clean` while the file goes unlinted).
        (cond (string? arg) (when-not (str/blank? arg) arg)
              (and (map? arg) (not (str/blank? (str (get arg "code"))))) (str (get arg "code"))
              :else nil)

        under
        (fn [p]
          (let [f (io/file (str p))]
            (str (if (.isAbsolute f) f (io/file root (str p))))))

        ;; `path` and `paths` are UNIONED, not shadowing — a model that emits BOTH
        ;; (e.g. a junk `path` beside a real `paths`) lints/validates every named
        ;; target instead of silently dropping one.
        requested
        (when-not code (into [] (distinct (concat (when path [path]) (when (seq paths) paths)))))

        ;; A named target that resolves to nothing must be an ERROR, not a silent
        ;; `clean`: a non-existent path expands to zero files → 0 findings, so the
        ;; model gets meaningless `clean` feedback with nothing to correct and spins.
        missing
        (into [] (remove #(.exists (io/file (under %))) requested))]

    (if (seq missing)
      (host/failure
        {:error {:message (str "lint target does not exist: "
                               (str/join ", " missing)
                               " — relative paths resolve against the workspace root")
                 :hint "pass an existing file/dir, or omit path/paths to lint the whole project"}})
      (let [targets
            (when (seq requested) (mapv #(relativize-path root (under %)) requested))

            base
            ;; clj-kondo is the STATIC provider (all branches). The :general provider
            ;; (reflection + boxed-math) is a COMPILER pass — the warnings only exist
            ;; while the code is compiled — so it runs over whatever we're TARGETING:
            ;; the `code` snippet, or each source file being linted (compiled in a
            ;; throwaway namespace that is torn down afterwards, so nothing leaks).
            ;; Its warnings merge into the flat findings and the warning count.
            (let [merge-general (fn [k g]
                                  (-> k
                                      (update "findings" into g)
                                      (update "warning" (fnil + 0) (count g))))]
              (if code
                (merge-general (lint/lint-code code) (reflection/compile-warnings code "<stdin>"))
                (let [src-files (expand-clj-source-files root
                                                         (if (seq requested)
                                                           (mapv under requested)
                                                           (discover-project-source-paths root)))]
                  (merge-general (lint-grouped src-files)
                                 (into []
                                       (mapcat #(reflection/compile-warnings (slurp (io/file %))
                                                                             (str %)))
                                       src-files)))))

            providers
            ["clj-kondo" "general"]

            findings
            (mapv #(update % "file" (partial relativize-path root)) (get base "findings"))]

        (host/success {:result (cond-> (assoc base
                                         "findings" findings
                                         "language" "clojure"
                                         "providers" providers)
                                 code
                                 (assoc "snippet" code)

                                 (seq targets)
                                 (assoc "targets" (vec targets)))})))))
