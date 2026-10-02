(ns com.blockether.vis.lang.clojure.repair
  "The delimiter repair `format` is allowed to WRITE, and what it must SAY about it.

   The repair is ADD-ONLY: a delimiter you omitted is added back, one you WROTE is
   never deleted. A lost opening `(` and one `)` too many are the same string, so
   deleting is a guess that rewrites code. `parinferish` proposes the candidate and
   `parinferish.balance` accepts it only when it provably parses and only ADDS — a
   file whose delimiters already balance is answered untouched."
  (:require [clojure.string :as str]
            [com.blockether.parinferish :as parinferish]
            [com.blockether.parinferish.balance :as balance]
            [com.blockether.vis.lang.clojure.syntax :as syntax]))

(defn- balancer
  "The candidate repair: parinfer in INDENT mode, which trusts the indentation the
   author wrote and closes what it says is open."
  [^String source]
  (try (parinferish/repair source {:mode :indent}) (catch Throwable _ nil)))

(defn repair-source
  "Repair `code`'s delimiters when they do not balance, over the whole file — the only
   span a formatter has.

   Answers `{:code <what to format> :repaired? bool :repairs [note] :why msg?}`. On a
   refusal the ORIGINAL code goes to the formatter untouched and `:why` names the
   mistake to look for, because a formatter that guesses which of the two happened is
   the corruption it was meant to prevent."
  [^String code]
  (if (or (str/blank? (str code)) (syntax/parses-clean? code))
    {:code code :repaired? false}
    (let [verdict (balance/rebalance {:balancer balancer
                                      :parses-clean? syntax/parses-clean?
                                      :source code
                                      :spans [[1 (max 1 (count (str/split-lines code)))]]
                                      :subject "this file has"})]
      (cond (:ok? verdict)
            {:code (:content verdict) :repaired? true :repairs (vec (:notes verdict))}
            (:why verdict) {:code code :repaired? false :why (:why verdict)}
            :else {:code code :repaired? false}))))
