(ns com.blockether.vis.lang.clojure.repair-test
  "Tests for the ADD-ONLY delimiter repair that formatting is allowed to apply: what
   reads as Clojure, what gets closed for you, and what is refused with a reason."
  (:require [clojure.string :as str]
            [com.blockether.vis.lang.clojure.repair :as repair]
            [com.blockether.vis.lang.clojure.syntax :as syntax]
            [lazytest.core :refer [defdescribe expect it]]))

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
             (it "leaves code that names an alias in a keyword untouched"
                 (let [code
                       "(ns app.core (:require [clojure.string :as str]))\n(def k ::str/join)\n"]
                   (expect (= {:code code :repaired? false} (repair/repair-source code)))))
             (it "closes delimiters the author left open, and says what it added"
                 (let [result (repair/repair-source "(defn f [x]\n  (inc x)\n")]
                   (expect (true? (:repaired? result)))
                   (expect (syntax/parses-clean? (:code result)))
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
