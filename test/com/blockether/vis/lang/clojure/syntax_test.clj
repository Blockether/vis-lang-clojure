(ns com.blockether.vis.lang.clojure.syntax-test
  "Tests for the Clojure reader's syntax verdict: source that reads, source that does
   not and where the reader stops, and what is never evaluated or loaded."
  (:require [com.blockether.vis.lang.clojure.syntax :as syntax]
            [lazytest.core :refer [defdescribe describe expect it]]))

(def ^:private clean-sources
  "Source that reads from end to end, each with what it exercises."
  [["an empty file" ""] ["only whitespace and commas" "  ,, \n\t"]
   ["a namespace and a function" "(ns app.core)\n(defn f [x] (inc x))\n"]
   ["a comment holding unbalanced delimiters" ";; ( [ { \" unbalanced\n(def x 1)\n"]
   ["a string holding delimiters" "(def s \"(((]]}}\")"]
   ["a string holding escaped quotes" "(def s \"say \\\"hi\\\"\")"]
   ["a string ending in an escaped backslash" "(def s \"C:\\\\dir\\\\\")"]
   ["a string with every simple escape" "(def s \"\\t\\r\\n\\b\\f\\\\\\\"\")"]
   ["a string with unicode and octal escapes" "(def s \"\\u00e9\\101\\0\")"]
   ["a string spanning lines" "(def s \"one\ntwo\n  three\")"]
   ["a string holding non-ASCII text" "(def s \"zażółć gęślą jaźń 🙂 日本\")"]
   ["a string holding a semicolon" "(def s \"; not a comment\")"]
   ["a string that looks like a regex and a character" "(def s \"#\\\"( \\\\a\")"]
   ["empty and adjacent strings" "[\"\" \"a\"\"b\"]"]
   ["a docstring holding code and markdown"
    "(defn f\n  \"Call `(f)` and get {:a 1}.\n   ```\n   (f]\n   ```\"\n  [])"]
   ["character literals"
    "[\\a \\Z \\newline \\space \\tab \\return \\backspace \\formfeed \\u0041 \\o101 \\( \\) \\[ \\] \\{ \\} \\\" \\\\ \\; \\,]"]
   ["a regex literal" "(re-find #\"\\d+\\s*[a-z]{2,}\" s)"]
   ["a regex holding an escaped quote and delimiters" "#\"\\\"[(]\\)\""]
   ["numbers in every literal form"
    "[1 -2 +3 1/2 -3/4 0x1F 0X1f 017 2r1010 36rZZ 1e10 1.5e-3 1.5M 10N ##Inf ##-Inf ##NaN]"]
   ["keywords, qualified and auto-resolved through an alias" "[:a :a/b ::c ::str/join :a.b/c]"]
   ["namespaced maps" "#:person{:name \"A\"} #::{:a 1} #::s{:b 2} #:a{:b 1 :_/c 2}"]
   ["metadata in every form" "^:private ^{:doc \"x\"} ^String #^Long (def x 1)"]
   ["quote, syntax-quote, unquote and deref" "'a `(b ~c ~@d) @e #'f"]
   ["syntax-quote with an alias, a gensym and a class" "`(s/join ~x) `foo# `Foo. `.method"]
   ["an anonymous function literal" "#(+ % %2 %&)"]
   ["discarded forms, stacked" "#_(foo bar) #_ #_ a b (baz)"]
   ["tagged literals the project defines" "#my/tag {:a 1} #js [1 2] #js {:a 1}"]
   ["inst and uuid literals"
    "#inst \"2020-01-01T00:00:00Z\" #uuid \"00000000-0000-0000-0000-000000000000\""]
   ["a reader conditional" "(def x #?(:clj 1 :cljs 2 :default 3))"]
   ["a splicing reader conditional" "[#?@(:clj [1 2] :cljs [3])]"]
   ["a ClojureScript-only branch" "#?(:cljs (js/console.log #js {:a 1}))"]
   ["a shebang line" "#!/usr/bin/env bb\n(println 1)\n"]
   ["trailing commas in collections" "{:a 1, :b 2,} [1, 2,]"]
   ["a leading byte-order mark" "\uFEFF(ns app.core)"]
   ["non-ASCII symbols" "(def zażółć 1) (def λ 2) (def ->x? 3)"]
   ["a set and a map with distinct keys" "#{1 2 3} {:a 1 \"a\" 2 'a 3}"]
   ["a deps.edn file"
    "{:paths [\"src\"]\n :deps {org.clojure/clojure {:mvn/version \"1.12.5\"}}\n :aliases {:test {:extra-paths [\"test\"]}}}\n"]
   ["a var quote and a symbolic value" "#'clojure.core/map ##Inf"]
   ["many top-level forms on one line" "(a) [b] {:c d} #{e} \"f\" \\g :h"]
   ["a comment form holding a string with a closing paren" "(comment\n  (f \")\")\n  ,)"]])

(def ^:private broken-sources
  "Source that does not read: what it exercises, the source, and where and why the
   reader stops."
  [["an unclosed list" "(defn f [x] (inc x)\n"
    {:line 1 :column 20 :message "EOF while reading, starting at line 1"}]
   ["a form left open across lines" "(ns a)\n\n(defn f [x]\n  (inc x)\n"
    {:line 4 :column 10 :message "EOF while reading, starting at line 3"}]
   ["one closing paren too many" "(defn f [x] (inc x)))\n"
    {:line 1 :column 22 :message "Unmatched delimiter: )"}]
   ["a lone closing paren" ")" {:line 1 :column 2 :message "Unmatched delimiter: )"}]
   ["a vector closed by a brace" "(let [a 1} a)"
    {:line 1 :column 11 :message "Unmatched delimiter: }"}]
   ["a vector closed by a paren" "[1 2)" {:line 1 :column 6 :message "Unmatched delimiter: )"}]
   ["an unclosed vector" "[1 2"
    {:line 1 :column 5 :message "EOF while reading, starting at line 1"}]
   ["an unclosed map" "{:a 1" {:line 1 :column 6 :message "EOF while reading, starting at line 1"}]
   ["an unclosed set" "#{1 2" {:line 1 :column 6 :message "EOF while reading, starting at line 1"}]
   ["an unterminated string" "(def s \"abc)\n"
    {:line 1 :column 13 :message "EOF while reading string"}]
   ["a string whose closing quote is escaped" "(def s \"abc\\\")"
    {:line 1 :column 15 :message "EOF while reading string"}]
   ["an unsupported string escape" "(def s \"\\x\")"
    {:line 1 :column 11 :message "Unsupported escape character: \\x"}]
   ["a malformed unicode escape in a string" "(def s \"\\uZZZZ\")"
    {:line 1 :column 12 :message "Invalid unicode escape: \\uZ"}]
   ["an octal string escape out of range" "(def s \"\\400\")"
    {:line 1 :column 13 :message "Octal escape sequence must be in range [0, 377]."}]
   ["an unknown character name" "[\\foo]"
    {:line 1 :column 6 :message "Unsupported character: \\foo"}]
   ["a backslash at the end of the file" "\\"
    {:line 1 :column 2 :message "EOF while reading character"}]
   ["an invalid regex" "#\"[unclosed\""
    {:line 1 :column 13 :message "Unclosed character class near index 8"}]
   ["an unterminated regex" "#\"abc" {:line 1 :column 6 :message "EOF while reading regex"}]
   ["a discarded form left open" "#_(foo\n(bar)"
    {:line 2 :column 6 :message "EOF while reading, starting at line 1"}]
   ["an invalid number" "(def x 1.2.3)" {:line 1 :column 13 :message "Invalid number: 1.2.3"}]
   ["a leading zero before an 8" "08" {:line 1 :column 3 :message "Invalid number: 08"}]
   ["a ratio with a zero denominator" "1/0" {:line 1 :column 4 :message "Divide by zero"}]
   ["a keyword ending in a slash" ":a/" {:line 1 :column 4 :message "Invalid token: :a/"}]
   ["a bare double colon" "::" {:line 1 :column 3 :message "Invalid token: ::"}]
   ["a map with an odd number of forms" "{:a}"
    {:line 1 :column 5 :message "Map literal must contain an even number of forms"}]
   ["a map with a duplicate key" "{:a 1 :a 2}" {:line 1 :column 12 :message "Duplicate key: :a"}]
   ["a set with a duplicate value" "#{1 1}" {:line 1 :column 7 :message "Duplicate key: 1"}]
   ["metadata that is a number" "^1 x"
    {:line 1 :column 3 :message "Metadata must be Symbol,Keyword,String,Vector or Map"}]
   ["metadata with no form after it" "^:private" {:line 1 :column 10 :message "EOF while reading"}]
   ["a nested anonymous function literal" "#(#(inc %))"
    {:line 1 :column 5 :message "Nested #()s are not allowed"}]
   ["a malformed inst literal" "#inst \"not-a-date\""
    {:line 1 :column 19 :message "Unrecognized date/time syntax: not-a-date"}]
   ["a malformed uuid literal" "#uuid \"nope\""
    {:line 1 :column 13 :message "Invalid UUID string: nope"}]
   ["read-eval" "#=(+ 1 2)"
    {:line 1 :column 3 :message "EvalReader not allowed when *read-eval* is false."}]
   ["a record literal" "#my.ns.Rec{:a 1}"
    {:line 1
     :column 17
     :message "Record construction syntax can only be used when *read-eval* == true"}]
   ["a reader conditional feature that is not a keyword" "#?(1 2)"
    {:line 1 :column 5 :message "Feature should be a keyword: 1"}]
   ["a splicing reader conditional at the top level" "#?@(:clj [1])"
    {:line 1 :column 14 :message "Reader conditional splicing not allowed at the top level."}]
   ["an unclosed form in another platform's branch" "#?(:clj 1 :cljs (oops)"
    {:line 1 :column 23 :message "EOF while reading, starting at line 1"}]
   ["an unknown symbolic value" "##Foo"
    {:line 1 :column 6 :message "Unknown symbolic value: ##Foo"}]
   ["a dispatch character with nothing after it" "#"
    {:line 1 :column 2 :message "EOF while reading character"}]
   ["a var quote with nothing after it" "#'" {:line 1 :column 3 :message "EOF while reading"}]
   ["a problem after valid forms" "(ns a)\n(def x 1)\n(def y [1 2)\n"
    {:line 3 :column 13 :message "Unmatched delimiter: )"}]
   ["a string opened inside a comment form" "(comment\n  \"open\n)"
    {:line 3 :column 2 :message "EOF while reading string"}]])

(defdescribe
  problem-test
  (describe "source that reads from end to end"
            (for [[what source] clean-sources]
              (it (str "reads " what)
                  (expect (nil? (syntax/problem source)))
                  (expect (true? (syntax/parses-clean? source))))))
  (describe "source that does not read"
            (for [[what source expected] broken-sources]
              (it (str "stops at " what)
                  (expect (= expected (syntax/problem source)))
                  (expect (false? (syntax/parses-clean? source))))))
  (it "gives no verdict for source nested deeper than the reader's stack"
      (let [deep (str (apply str (repeat 100000 "(")) (apply str (repeat 100000 ")")))]
        (expect (nil? (syntax/problem deep)))))
  (it "never evaluates what it reads"
      (let [marker (java.io.File/createTempFile "vis-lang-syntax" ".never")]
        (.delete marker)
        (expect (some? (syntax/problem (str "#=(spit " (pr-str (str marker)) " \"x\")"))))
        (expect (false? (.exists marker)))))
  (it "never loads the namespace an alias names"
      (expect (nil? (syntax/problem (str "(ns a (:require [vis.never.loaded :as never]))\n"
                                         "::never/k `never/f #::never{:a 1}"))))
      (expect (nil? (find-ns 'vis.never.loaded)))))

(defdescribe
  check-test
  (it "reports each source that does not read, by path, and skips the clean ones"
      (expect (= [{:path "a.clj" :line 1 :column 3 :message "EOF while reading, starting at line 1"}
                  {:path "z.edn"
                   :line 1
                   :column 5
                   :message "Map literal must contain an even number of forms"}]
                 (syntax/check [["z.edn" "{:a}"] ["ok.cljc" "#?(:clj 1)"] ["a.clj" "(a"]]))))
  (it "answers an empty vector when every source reads"
      (expect (= [] (syntax/check {"a.clj" "(a)" "b.cljs" "#js {}"}))))
  (it "answers an empty vector for no sources" (expect (= [] (syntax/check [])))))
