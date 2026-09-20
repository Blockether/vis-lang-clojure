(ns build
  "Build and deploy vis-lang-clojure: one library jar on Clojars, which is how a
   project gets these tools — `clojure -Sdeps '{:deps {com.blockether/vis-lang-clojure
   {:mvn/version \"…\"}}}' -M -m com.blockether.vis.lang.clojure.cli`."
  (:require [clojure.string :as str]
            [clojure.tools.build.api :as b]
            [deps-deploy.deps-deploy :as dd]))

(def lib 'com.blockether/vis-lang-clojure)

(def declared-version
  "This library's own release number. The repo-root `VERSION` file is its single
   source of truth; the release tag mirrors it."
  (str/trim (slurp "VERSION")))

(def version
  "What an artifact is stamped with. CI exports VIS_LANG_CLOJURE_VERSION from the
   release tag and publishes that exact number; every other build is a `-SNAPSHOT`,
   so a local install cannot shadow a release in ~/.m2."
  (if-let [tag (System/getenv "VIS_LANG_CLOJURE_VERSION")]
    (str/replace tag #"^v" "")
    (str declared-version "-SNAPSHOT")))

(defn- check-version!
  "Refuse to build artifacts whose version sources disagree: the tag names the
   Clojars coordinate and `VERSION` is what the pom declares, so drift between them
   publishes a version nobody asked for."
  []
  (let [release (str/replace version #"-SNAPSHOT$" "")]
    (when-not (= release declared-version)
      (throw (ex-info (format "version mismatch: tag %s, VERSION %s" release declared-version)
                      {:release release :declared declared-version})))))

(def class-dir "target/classes")

(def jar-file (format "target/%s.jar" (name lib)))

(def basis (delay (b/create-basis {:project "deps.edn"})))

(defn clean [_] (b/delete {:path "target"}))

(defn- pom-data
  []
  [[:description
    "Clojure tools for Vis: format, lint, tests, managed nREPL and an add-only delimiter repair."]
   [:url "https://github.com/Blockether/vis-lang-clojure"]
   [:licenses
    [:license [:name "Apache License 2.0"] [:url "https://www.apache.org/licenses/LICENSE-2.0"]]]
   [:scm [:url "https://github.com/Blockether/vis-lang-clojure"]
    [:connection "scm:git:https://github.com/Blockether/vis-lang-clojure.git"]
    [:developerConnection "scm:git:ssh://git@github.com/Blockether/vis-lang-clojure.git"]]])

(defn jar
  [_]
  (check-version!)
  (clean nil)
  (b/write-pom {:class-dir class-dir
                :lib lib
                :version version
                :basis @basis
                :src-dirs ["src"]
                :pom-data (pom-data)})
  (b/copy-dir {:src-dirs ["src"] :target-dir class-dir})
  (b/copy-file {:src "LICENSE" :target (str class-dir "/META-INF/LICENSE")})
  (b/jar {:class-dir class-dir :jar-file jar-file})
  (println "Built:" jar-file "version:" version))

(defn deploy
  [_]
  (jar nil)
  (dd/deploy
    {:installer :remote :artifact jar-file :pom-file (b/pom-path {:lib lib :class-dir class-dir})}))

(defn install
  [_]
  (jar nil)
  (dd/deploy
    {:installer :local :artifact jar-file :pom-file (b/pom-path {:lib lib :class-dir class-dir})}))
