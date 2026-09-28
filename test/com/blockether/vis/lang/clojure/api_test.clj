(ns com.blockether.vis.lang.clojure.api-test
  (:require [clojure.java.io :as io]
            [clojure.string :as str]
            [com.blockether.vis.lang.clojure.api :as api]
            [lazytest.core :refer [defdescribe expect it]])
  (:import (java.nio.file Files)
           (java.nio.file.attribute FileAttribute)))

(defn- temp-dir
  "A fresh empty directory, so a file test never touches the repository."
  ^java.io.File []
  (.toFile (Files/createTempDirectory "vis-lang-clojure-api" (into-array FileAttribute []))))

(defn- result
  "The `:result` of a successful envelope, or a failure the test can read."
  [envelope]
  (expect (:success? envelope) (pr-str envelope))
  (:result envelope))

;; A code string is formatted in memory: the caller gets the formatted TEXT
;; back, because nothing was written for it to read.
(defdescribe
  clj-format-fn-test
  (it "answers the formatted text of a code string"
      (let [r (result (api/clj-format-fn {:workspace/root "."} {"code" "(defn f [x]\n(* x 2))"}))]
        (expect (true? (get r "changed")))
        (expect (str/starts-with? (get r "text") "(defn f [x]"))
        (expect (str/includes? (get r "text") "(* x 2)"))
        (expect (not= "(defn f [x]\n(* x 2))" (get r "text")))
        (expect (nil? (get r "path")))))
  (it "completes a delimiter the source omitted, and names what it added"
      (let [r (result (api/clj-format-fn {:workspace/root "."} {"code" "(defn f [x]\n(* x 2)"}))]
        (expect (true? (get r "repaired")))
        (expect (str/ends-with? (str/trim (get r "text")) ")"))
        (expect (seq (get r "repairs")))))
  (it "formats a file in place, reporting the path instead of its text"
      (let [dir
            (temp-dir)

            file
            (io/file dir "sample.clj")]

        (spit file "(defn f [x]\n(* x 2))")
        (let [r (result (api/clj-format-fn {:workspace/root (str dir)} {"path" "sample.clj"}))]
          (expect (= "sample.clj" (get r "path")))
          (expect (true? (get r "wrote")))
          (expect (nil? (get r "text")))
          (expect (str/includes? (slurp file) "\n  (* x 2))")))))
  (it "counts the lines formatting added and removed, a rewritten line in each"
      (let [changes @#'api/line-changes]
        (expect (= {"added" 0 "removed" 0} (changes "(ns a)\n" "(ns a)\n")))
        (expect (= {"added" 1 "removed" 1} (changes "a\nb\nc" "a\nB\nc")))
        (expect (= {"added" 1 "removed" 0} (changes "a\nb" "a\nb\n")))
        (expect (= {"added" 2 "removed" 2} (changes "a\nb\nc\nd" "a\nc\nx\ny")))
        (expect (= {"added" 1 "removed" 1} (changes "a\nb" "b\na")))))
  (it "reports the changed lines of a code string, of each file and of a batch"
      (let [dir (temp-dir)]
        (spit (io/file dir "a.clj") "(defn f [x]\n(* x 2))\n")
        (spit (io/file dir "b.clj") "(defn g [x]\n  (* x 3))\n")
        (let [code (result (api/clj-format-fn {:workspace/root (str dir)}
                                              {"code" "(defn f [x]\n(* x 2))\n"}))
              batch (result (api/clj-format-fn {:workspace/root (str dir)} {"paths" ["."]}))]

          (expect (= [1 1] [(get code "added") (get code "removed")]))
          (expect (= 1 (get batch "changed")))
          (expect (= [1 1] [(get batch "added") (get batch "removed")]))
          (expect (= {"a.clj" [1 1] "b.clj" [0 0]}
                     (into {}
                           (map (juxt #(get % "path") (juxt #(get % "added") #(get % "removed"))))
                           (get batch "files"))))))))

(defdescribe clj-lint-fn-test
             (it "reports a finding with its level, location and provider"
                 (let [r
                       (result (api/clj-lint-fn {:workspace/root "."}
                                                {"code" "(defn f [x] (inc x))\n(f 1 2)"}))

                       finding
                       (first (get r "findings"))]

                   (expect (= 1 (get r "error")))
                   (expect (= "clojure" (get r "language")))
                   (expect (= ["clj-kondo" "general"] (get r "providers")))
                   (expect (= "<stdin>" (get finding "file")))
                   (expect (= 2 (get finding "row")))
                   (expect (= "error" (get finding "level")))
                   (expect (= "clj-kondo" (get finding "provider")))
                   (expect (str/includes? (get finding "message") "expects 1"))))
             (it "refuses a target that does not exist instead of reporting it clean"
                 (let [envelope (api/clj-lint-fn {:workspace/root "."} {"paths" ["no/such/dir"]})]
                   (expect (false? (:success? envelope)))
                   (expect (str/includes? (get-in envelope [:error :message]) "no/such/dir")))))
