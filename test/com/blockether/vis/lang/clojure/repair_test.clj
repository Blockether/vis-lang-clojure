(ns com.blockether.vis.lang.clojure.repair-test
  "Tests for the ADD-ONLY delimiter repair that formatting is allowed to apply: what
   reads as Clojure, what gets closed for you, and what is refused with a reason."
  (:require [clojure.string :as str]
            [com.blockether.vis.lang.clojure.repair :as repair]
            [lazytest.core :refer [defdescribe expect it]]))

(defdescribe parses-clean?-test
             (it "accepts source that reads from end to end"
                 (expect (repair/parses-clean? "(ns app.core)\n(defn f [x] (inc x))\n"))
                 (expect (repair/parses-clean? ""))
                 (expect (repair/parses-clean? ";; only a comment\n")))
             (it "accepts a reader conditional, so a .cljc file is never called broken"
                 (expect (repair/parses-clean? "(def x #?(:clj 1 :cljs 2))\n")))
             (it "accepts an unknown tagged literal, reading it as its value"
                 (expect (repair/parses-clean? "(def x #project/widget {:id 1})\n")))
             (it "rejects source whose delimiters do not close"
                 (expect (false? (repair/parses-clean? "(defn f [x] (inc x)\n")))
                 (expect (false? (repair/parses-clean? "(def s \"unterminated\n"))))
             (it "never evaluates what it reads: read-eval syntax is refused, not run"
                 (expect (false? (repair/parses-clean? "#=(spit \"/tmp/vis-lang-never\" \"x\")\n")))
                 (expect (false? (.exists (java.io.File. "/tmp/vis-lang-never"))))))

(defdescribe repair-source-test
             (it "leaves balanced code exactly as it was"
                 (let [code
                       "(ns app.core)\n\n(defn f [x] (inc x))\n"

                       result
                       (repair/repair-source code)]

                   (expect (= code (:code result)))
                   (expect (false? (:repaired? result)))
                   (expect (nil? (:why result)))))
             (it "leaves blank source alone"
                 (expect (= {:code "" :repaired? false} (repair/repair-source "")))
                 (expect (= {:code "   \n" :repaired? false} (repair/repair-source "   \n"))))
             (it "closes delimiters the author left open, and says what it added"
                 (let [result (repair/repair-source "(defn f [x]\n  (inc x)\n")]
                   (expect (true? (:repaired? result)))
                   (expect (repair/parses-clean? (:code result)))
                   (expect (str/starts-with? (:code result) "(defn f [x]"))
                   (expect (seq (:repairs result)))))
             (it "only ADDS: a repaired file still contains every delimiter that was written"
                 (let [code
                       "(defn f [x]\n  (inc x)\n"

                       result
                       (repair/repair-source code)]

                   (expect (>= (count (re-seq #"\(" (:code result))) (count (re-seq #"\(" code))))
                   (expect (>= (count (re-seq #"\)" (:code result))) (count (re-seq #"\)" code))))
                   (expect (= (count (re-seq #"\[" code)) (count (re-seq #"\[" (:code result)))))))
             (it "refuses a file it cannot fix by adding, and hands back the original with a reason"
                 (let [code
                       "(defn f [x] (inc x)))\n"

                       result
                       (repair/repair-source code)]

                   (expect (= code (:code result)))
                   (expect (false? (:repaired? result)))
                   (expect (string? (:why result)))
                   (expect (str/includes? (:why result) "this file has")))))
