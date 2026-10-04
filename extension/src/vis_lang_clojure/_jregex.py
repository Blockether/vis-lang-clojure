"""Whether `java.util.regex.Pattern` compiles a pattern, without a JVM.

Clojure compiles a regex literal while it reads, so a pattern that Java refuses
makes the whole file unreadable. `problem` answers with the first line of Java's
message, or None. The parser follows `Pattern` of JDK 25: groups, classes,
escapes, quantifiers, inline flags and the names of blocks, scripts and
properties. Character names come from Python's `unicodedata`, which has the
Unicode version of JDK 25 (16.0) from Python 3.14. With an older table, an
unknown character name gives no verdict. The parser does not check the block in
the block form of a character name or look-behind lengths, so it never refuses
a pattern that Java compiles.
"""

import re
import unicodedata

_MAX_REPS = 0x7FFFFFFF
_SPACE = frozenset(map(ord, " \t\n\x0b\f\r"))
_POSIX_UPPER = frozenset(
    "ALPHA LOWER UPPER SPACE PUNCT XDIGIT ALNUM CNTRL DIGIT BLANK GRAPH PRINT".split()
)
_PROPERTIES = frozenset(
    (
        "Cn Lu Ll Lt Lm Lo Mn Me Mc Nd Nl No Zs Zl Zp Cc Cf Co Cs Pd Ps Pe Pc Po Sm Sc"
        " Sk So Pi Pf L M N Z C P S LC LD L1 all ASCII Alnum Alpha Blank Cntrl Digit"
        " Graph Lower Print Punct Space Upper XDigit javaLowerCase javaUpperCase"
        " javaAlphabetic javaIdeographic javaTitleCase javaDigit javaDefined javaLetter"
        " javaLetterOrDigit javaJavaIdentifierStart javaJavaIdentifierPart"
        " javaUnicodeIdentifierStart javaUnicodeIdentifierPart javaIdentifierIgnorable"
        " javaSpaceChar javaWhitespace javaISOControl javaMirrored"
    ).split()
)
_COMMENTS = 0x04
_UNIX_LINES = 0x01
_UNICODE_CHARACTER_CLASS = 0x100
_FLAGS = {
    "i": 0x02,
    "m": 0x08,
    "s": 0x20,
    "d": _UNIX_LINES,
    "u": 0x40,
    "c": 0x80,
    "x": _COMMENTS,
    "U": _UNICODE_CHARACTER_CLASS | 0x40,
}

# The keys of `Character.UnicodeBlock.forName` (JDK 25, Unicode 16.0), upper case.
_BLOCKS = frozenset(
    (
        "ADLAM|AEGEAN NUMBERS|AEGEANNUMBERS|AEGEAN_NUMBERS|AHOM|ALCHEMICAL SYMBOLS|"
        "ALCHEMICALSYMBOLS|ALCHEMICAL_SYMBOLS|ALPHABETIC PRESENTATION FORMS|"
        "ALPHABETICPRESENTATIONFORMS|ALPHABETIC_PRESENTATION_FORMS|"
        "ANATOLIAN HIEROGLYPHS|ANATOLIANHIEROGLYPHS|ANATOLIAN_HIEROGLYPHS|"
        "ANCIENT GREEK MUSICAL NOTATION|ANCIENT GREEK NUMBERS|ANCIENT SYMBOLS|"
        "ANCIENTGREEKMUSICALNOTATION|ANCIENTGREEKNUMBERS|ANCIENTSYMBOLS|"
        "ANCIENT_GREEK_MUSICAL_NOTATION|ANCIENT_GREEK_NUMBERS|ANCIENT_SYMBOLS|ARABIC|"
        "ARABIC EXTENDED-A|ARABIC EXTENDED-B|ARABIC EXTENDED-C|"
        "ARABIC MATHEMATICAL ALPHABETIC SYMBOLS|ARABIC PRESENTATION FORMS-A|"
        "ARABIC PRESENTATION FORMS-B|ARABIC SUPPLEMENT|ARABICEXTENDED-A|"
        "ARABICEXTENDED-B|ARABICEXTENDED-C|ARABICMATHEMATICALALPHABETICSYMBOLS|"
        "ARABICPRESENTATIONFORMS-A|ARABICPRESENTATIONFORMS-B|ARABICSUPPLEMENT|"
        "ARABIC_EXTENDED_A|ARABIC_EXTENDED_B|ARABIC_EXTENDED_C|"
        "ARABIC_MATHEMATICAL_ALPHABETIC_SYMBOLS|ARABIC_PRESENTATION_FORMS_A|"
        "ARABIC_PRESENTATION_FORMS_B|ARABIC_SUPPLEMENT|ARMENIAN|ARROWS|AVESTAN|"
        "BALINESE|BAMUM|BAMUM SUPPLEMENT|BAMUMSUPPLEMENT|BAMUM_SUPPLEMENT|BASIC LATIN|"
        "BASICLATIN|BASIC_LATIN|BASSA VAH|BASSAVAH|BASSA_VAH|BATAK|BENGALI|BHAIKSUKI|"
        "BLOCK ELEMENTS|BLOCKELEMENTS|BLOCK_ELEMENTS|BOPOMOFO|BOPOMOFO EXTENDED|"
        "BOPOMOFOEXTENDED|BOPOMOFO_EXTENDED|BOX DRAWING|BOXDRAWING|BOX_DRAWING|BRAHMI|"
        "BRAILLE PATTERNS|BRAILLEPATTERNS|BRAILLE_PATTERNS|BUGINESE|BUHID|"
        "BYZANTINE MUSICAL SYMBOLS|BYZANTINEMUSICALSYMBOLS|BYZANTINE_MUSICAL_SYMBOLS|"
        "CARIAN|CAUCASIAN ALBANIAN|CAUCASIANALBANIAN|CAUCASIAN_ALBANIAN|CHAKMA|CHAM|"
        "CHEROKEE|CHEROKEE SUPPLEMENT|CHEROKEESUPPLEMENT|CHEROKEE_SUPPLEMENT|"
        "CHESS SYMBOLS|CHESSSYMBOLS|CHESS_SYMBOLS|CHORASMIAN|CJK COMPATIBILITY|"
        "CJK COMPATIBILITY FORMS|CJK COMPATIBILITY IDEOGRAPHS|"
        "CJK COMPATIBILITY IDEOGRAPHS SUPPLEMENT|CJK RADICALS SUPPLEMENT|CJK STROKES|"
        "CJK SYMBOLS AND PUNCTUATION|CJK UNIFIED IDEOGRAPHS|"
        "CJK UNIFIED IDEOGRAPHS EXTENSION A|CJK UNIFIED IDEOGRAPHS EXTENSION B|"
        "CJK UNIFIED IDEOGRAPHS EXTENSION C|CJK UNIFIED IDEOGRAPHS EXTENSION D|"
        "CJK UNIFIED IDEOGRAPHS EXTENSION E|CJK UNIFIED IDEOGRAPHS EXTENSION F|"
        "CJK UNIFIED IDEOGRAPHS EXTENSION G|CJK UNIFIED IDEOGRAPHS EXTENSION H|"
        "CJK UNIFIED IDEOGRAPHS EXTENSION I|CJKCOMPATIBILITY|CJKCOMPATIBILITYFORMS|"
        "CJKCOMPATIBILITYIDEOGRAPHS|CJKCOMPATIBILITYIDEOGRAPHSSUPPLEMENT|"
        "CJKRADICALSSUPPLEMENT|CJKSTROKES|CJKSYMBOLSANDPUNCTUATION|"
        "CJKUNIFIEDIDEOGRAPHS|CJKUNIFIEDIDEOGRAPHSEXTENSIONA|"
        "CJKUNIFIEDIDEOGRAPHSEXTENSIONB|CJKUNIFIEDIDEOGRAPHSEXTENSIONC|"
        "CJKUNIFIEDIDEOGRAPHSEXTENSIOND|CJKUNIFIEDIDEOGRAPHSEXTENSIONE|"
        "CJKUNIFIEDIDEOGRAPHSEXTENSIONF|CJKUNIFIEDIDEOGRAPHSEXTENSIONG|"
        "CJKUNIFIEDIDEOGRAPHSEXTENSIONH|CJKUNIFIEDIDEOGRAPHSEXTENSIONI|"
        "CJK_COMPATIBILITY|CJK_COMPATIBILITY_FORMS|CJK_COMPATIBILITY_IDEOGRAPHS|"
        "CJK_COMPATIBILITY_IDEOGRAPHS_SUPPLEMENT|CJK_RADICALS_SUPPLEMENT|CJK_STROKES|"
        "CJK_SYMBOLS_AND_PUNCTUATION|CJK_UNIFIED_IDEOGRAPHS|"
        "CJK_UNIFIED_IDEOGRAPHS_EXTENSION_A|CJK_UNIFIED_IDEOGRAPHS_EXTENSION_B|"
        "CJK_UNIFIED_IDEOGRAPHS_EXTENSION_C|CJK_UNIFIED_IDEOGRAPHS_EXTENSION_D|"
        "CJK_UNIFIED_IDEOGRAPHS_EXTENSION_E|CJK_UNIFIED_IDEOGRAPHS_EXTENSION_F|"
        "CJK_UNIFIED_IDEOGRAPHS_EXTENSION_G|CJK_UNIFIED_IDEOGRAPHS_EXTENSION_H|"
        "CJK_UNIFIED_IDEOGRAPHS_EXTENSION_I|COMBINING DIACRITICAL MARKS|"
        "COMBINING DIACRITICAL MARKS EXTENDED|COMBINING DIACRITICAL MARKS FOR SYMBOLS|"
        "COMBINING DIACRITICAL MARKS SUPPLEMENT|COMBINING HALF MARKS|"
        "COMBINING MARKS FOR SYMBOLS|COMBININGDIACRITICALMARKS|"
        "COMBININGDIACRITICALMARKSEXTENDED|COMBININGDIACRITICALMARKSFORSYMBOLS|"
        "COMBININGDIACRITICALMARKSSUPPLEMENT|COMBININGHALFMARKS|"
        "COMBININGMARKSFORSYMBOLS|COMBINING_DIACRITICAL_MARKS|"
        "COMBINING_DIACRITICAL_MARKS_EXTENDED|COMBINING_DIACRITICAL_MARKS_SUPPLEMENT|"
        "COMBINING_HALF_MARKS|COMBINING_MARKS_FOR_SYMBOLS|COMMON INDIC NUMBER FORMS|"
        "COMMONINDICNUMBERFORMS|COMMON_INDIC_NUMBER_FORMS|CONTROL PICTURES|"
        "CONTROLPICTURES|CONTROL_PICTURES|COPTIC|COPTIC EPACT NUMBERS|"
        "COPTICEPACTNUMBERS|COPTIC_EPACT_NUMBERS|COUNTING ROD NUMERALS|"
        "COUNTINGRODNUMERALS|COUNTING_ROD_NUMERALS|CUNEIFORM|"
        "CUNEIFORM NUMBERS AND PUNCTUATION|CUNEIFORMNUMBERSANDPUNCTUATION|"
        "CUNEIFORM_NUMBERS_AND_PUNCTUATION|CURRENCY SYMBOLS|CURRENCYSYMBOLS|"
        "CURRENCY_SYMBOLS|CYPRIOT SYLLABARY|CYPRIOTSYLLABARY|CYPRIOT_SYLLABARY|"
        "CYPRO-MINOAN|CYPRO_MINOAN|CYRILLIC|CYRILLIC EXTENDED-A|CYRILLIC EXTENDED-B|"
        "CYRILLIC EXTENDED-C|CYRILLIC EXTENDED-D|CYRILLIC SUPPLEMENT|"
        "CYRILLIC SUPPLEMENTARY|CYRILLICEXTENDED-A|CYRILLICEXTENDED-B|"
        "CYRILLICEXTENDED-C|CYRILLICEXTENDED-D|CYRILLICSUPPLEMENT|"
        "CYRILLICSUPPLEMENTARY|CYRILLIC_EXTENDED_A|CYRILLIC_EXTENDED_B|"
        "CYRILLIC_EXTENDED_C|CYRILLIC_EXTENDED_D|CYRILLIC_SUPPLEMENTARY|DESERET|"
        "DEVANAGARI|DEVANAGARI EXTENDED|DEVANAGARI EXTENDED-A|DEVANAGARIEXTENDED|"
        "DEVANAGARIEXTENDED-A|DEVANAGARI_EXTENDED|DEVANAGARI_EXTENDED_A|DINGBATS|"
        "DIVES AKURU|DIVESAKURU|DIVES_AKURU|DOGRA|DOMINO TILES|DOMINOTILES|"
        "DOMINO_TILES|DUPLOYAN|EARLY DYNASTIC CUNEIFORM|EARLYDYNASTICCUNEIFORM|"
        "EARLY_DYNASTIC_CUNEIFORM|EGYPTIAN HIEROGLYPH FORMAT CONTROLS|"
        "EGYPTIAN HIEROGLYPHS|EGYPTIAN HIEROGLYPHS EXTENDED-A|"
        "EGYPTIANHIEROGLYPHFORMATCONTROLS|EGYPTIANHIEROGLYPHS|"
        "EGYPTIANHIEROGLYPHSEXTENDED-A|EGYPTIAN_HIEROGLYPHS|"
        "EGYPTIAN_HIEROGLYPHS_EXTENDED_A|EGYPTIAN_HIEROGLYPH_FORMAT_CONTROLS|ELBASAN|"
        "ELYMAIC|EMOTICONS|ENCLOSED ALPHANUMERIC SUPPLEMENT|ENCLOSED ALPHANUMERICS|"
        "ENCLOSED CJK LETTERS AND MONTHS|ENCLOSED IDEOGRAPHIC SUPPLEMENT|"
        "ENCLOSEDALPHANUMERICS|ENCLOSEDALPHANUMERICSUPPLEMENT|"
        "ENCLOSEDCJKLETTERSANDMONTHS|ENCLOSEDIDEOGRAPHICSUPPLEMENT|"
        "ENCLOSED_ALPHANUMERICS|ENCLOSED_ALPHANUMERIC_SUPPLEMENT|"
        "ENCLOSED_CJK_LETTERS_AND_MONTHS|ENCLOSED_IDEOGRAPHIC_SUPPLEMENT|ETHIOPIC|"
        "ETHIOPIC EXTENDED|ETHIOPIC EXTENDED-A|ETHIOPIC EXTENDED-B|ETHIOPIC SUPPLEMENT|"
        "ETHIOPICEXTENDED|ETHIOPICEXTENDED-A|ETHIOPICEXTENDED-B|ETHIOPICSUPPLEMENT|"
        "ETHIOPIC_EXTENDED|ETHIOPIC_EXTENDED_A|ETHIOPIC_EXTENDED_B|ETHIOPIC_SUPPLEMENT|"
        "GARAY|GENERAL PUNCTUATION|GENERALPUNCTUATION|GENERAL_PUNCTUATION|"
        "GEOMETRIC SHAPES|GEOMETRIC SHAPES EXTENDED|GEOMETRICSHAPES|"
        "GEOMETRICSHAPESEXTENDED|GEOMETRIC_SHAPES|GEOMETRIC_SHAPES_EXTENDED|GEORGIAN|"
        "GEORGIAN EXTENDED|GEORGIAN SUPPLEMENT|GEORGIANEXTENDED|GEORGIANSUPPLEMENT|"
        "GEORGIAN_EXTENDED|GEORGIAN_SUPPLEMENT|GLAGOLITIC|GLAGOLITIC SUPPLEMENT|"
        "GLAGOLITICSUPPLEMENT|GLAGOLITIC_SUPPLEMENT|GOTHIC|GRANTHA|GREEK|"
        "GREEK AND COPTIC|GREEK EXTENDED|GREEKANDCOPTIC|GREEKEXTENDED|GREEK_EXTENDED|"
        "GUJARATI|GUNJALA GONDI|GUNJALAGONDI|GUNJALA_GONDI|GURMUKHI|GURUNG KHEMA|"
        "GURUNGKHEMA|GURUNG_KHEMA|HALFWIDTH AND FULLWIDTH FORMS|"
        "HALFWIDTHANDFULLWIDTHFORMS|HALFWIDTH_AND_FULLWIDTH_FORMS|"
        "HANGUL COMPATIBILITY JAMO|HANGUL JAMO|HANGUL JAMO EXTENDED-A|"
        "HANGUL JAMO EXTENDED-B|HANGUL SYLLABLES|HANGULCOMPATIBILITYJAMO|HANGULJAMO|"
        "HANGULJAMOEXTENDED-A|HANGULJAMOEXTENDED-B|HANGULSYLLABLES|"
        "HANGUL_COMPATIBILITY_JAMO|HANGUL_JAMO|HANGUL_JAMO_EXTENDED_A|"
        "HANGUL_JAMO_EXTENDED_B|HANGUL_SYLLABLES|HANIFI ROHINGYA|HANIFIROHINGYA|"
        "HANIFI_ROHINGYA|HANUNOO|HATRAN|HEBREW|HIGH PRIVATE USE SURROGATES|"
        "HIGH SURROGATES|HIGHPRIVATEUSESURROGATES|HIGHSURROGATES|"
        "HIGH_PRIVATE_USE_SURROGATES|HIGH_SURROGATES|HIRAGANA|"
        "IDEOGRAPHIC DESCRIPTION CHARACTERS|IDEOGRAPHIC SYMBOLS AND PUNCTUATION|"
        "IDEOGRAPHICDESCRIPTIONCHARACTERS|IDEOGRAPHICSYMBOLSANDPUNCTUATION|"
        "IDEOGRAPHIC_DESCRIPTION_CHARACTERS|IDEOGRAPHIC_SYMBOLS_AND_PUNCTUATION|"
        "IMPERIAL ARAMAIC|IMPERIALARAMAIC|IMPERIAL_ARAMAIC|INDIC SIYAQ NUMBERS|"
        "INDICSIYAQNUMBERS|INDIC_SIYAQ_NUMBERS|INSCRIPTIONAL PAHLAVI|"
        "INSCRIPTIONAL PARTHIAN|INSCRIPTIONALPAHLAVI|INSCRIPTIONALPARTHIAN|"
        "INSCRIPTIONAL_PAHLAVI|INSCRIPTIONAL_PARTHIAN|IPA EXTENSIONS|IPAEXTENSIONS|"
        "IPA_EXTENSIONS|JAVANESE|KAITHI|KAKTOVIK NUMERALS|KAKTOVIKNUMERALS|"
        "KAKTOVIK_NUMERALS|KANA EXTENDED-A|KANA EXTENDED-B|KANA SUPPLEMENT|"
        "KANAEXTENDED-A|KANAEXTENDED-B|KANASUPPLEMENT|KANA_EXTENDED_A|KANA_EXTENDED_B|"
        "KANA_SUPPLEMENT|KANBUN|KANGXI RADICALS|KANGXIRADICALS|KANGXI_RADICALS|KANNADA|"
        "KATAKANA|KATAKANA PHONETIC EXTENSIONS|KATAKANAPHONETICEXTENSIONS|"
        "KATAKANA_PHONETIC_EXTENSIONS|KAWI|KAYAH LI|KAYAHLI|KAYAH_LI|KHAROSHTHI|"
        "KHITAN SMALL SCRIPT|KHITANSMALLSCRIPT|KHITAN_SMALL_SCRIPT|KHMER|KHMER SYMBOLS|"
        "KHMERSYMBOLS|KHMER_SYMBOLS|KHOJKI|KHUDAWADI|KIRAT RAI|KIRATRAI|KIRAT_RAI|LAO|"
        "LATIN EXTENDED ADDITIONAL|LATIN EXTENDED-A|LATIN EXTENDED-B|LATIN EXTENDED-C|"
        "LATIN EXTENDED-D|LATIN EXTENDED-E|LATIN EXTENDED-F|LATIN EXTENDED-G|"
        "LATIN-1 SUPPLEMENT|LATIN-1SUPPLEMENT|LATINEXTENDED-A|LATINEXTENDED-B|"
        "LATINEXTENDED-C|LATINEXTENDED-D|LATINEXTENDED-E|LATINEXTENDED-F|"
        "LATINEXTENDED-G|LATINEXTENDEDADDITIONAL|LATIN_1_SUPPLEMENT|LATIN_EXTENDED_A|"
        "LATIN_EXTENDED_ADDITIONAL|LATIN_EXTENDED_B|LATIN_EXTENDED_C|LATIN_EXTENDED_D|"
        "LATIN_EXTENDED_E|LATIN_EXTENDED_F|LATIN_EXTENDED_G|LEPCHA|LETTERLIKE SYMBOLS|"
        "LETTERLIKESYMBOLS|LETTERLIKE_SYMBOLS|LIMBU|LINEAR A|LINEAR B IDEOGRAMS|"
        "LINEAR B SYLLABARY|LINEARA|LINEARBIDEOGRAMS|LINEARBSYLLABARY|LINEAR_A|"
        "LINEAR_B_IDEOGRAMS|LINEAR_B_SYLLABARY|LISU|LISU SUPPLEMENT|LISUSUPPLEMENT|"
        "LISU_SUPPLEMENT|LOW SURROGATES|LOWSURROGATES|LOW_SURROGATES|LYCIAN|LYDIAN|"
        "MAHAJANI|MAHJONG TILES|MAHJONGTILES|MAHJONG_TILES|MAKASAR|MALAYALAM|MANDAIC|"
        "MANICHAEAN|MARCHEN|MASARAM GONDI|MASARAMGONDI|MASARAM_GONDI|"
        "MATHEMATICAL ALPHANUMERIC SYMBOLS|MATHEMATICAL OPERATORS|"
        "MATHEMATICALALPHANUMERICSYMBOLS|MATHEMATICALOPERATORS|"
        "MATHEMATICAL_ALPHANUMERIC_SYMBOLS|MATHEMATICAL_OPERATORS|MAYAN NUMERALS|"
        "MAYANNUMERALS|MAYAN_NUMERALS|MEDEFAIDRIN|MEETEI MAYEK|MEETEI MAYEK EXTENSIONS|"
        "MEETEIMAYEK|MEETEIMAYEKEXTENSIONS|MEETEI_MAYEK|MEETEI_MAYEK_EXTENSIONS|"
        "MENDE KIKAKUI|MENDEKIKAKUI|MENDE_KIKAKUI|MEROITIC CURSIVE|"
        "MEROITIC HIEROGLYPHS|MEROITICCURSIVE|MEROITICHIEROGLYPHS|MEROITIC_CURSIVE|"
        "MEROITIC_HIEROGLYPHS|MIAO|MISCELLANEOUS MATHEMATICAL SYMBOLS-A|"
        "MISCELLANEOUS MATHEMATICAL SYMBOLS-B|MISCELLANEOUS SYMBOLS|"
        "MISCELLANEOUS SYMBOLS AND ARROWS|MISCELLANEOUS SYMBOLS AND PICTOGRAPHS|"
        "MISCELLANEOUS TECHNICAL|MISCELLANEOUSMATHEMATICALSYMBOLS-A|"
        "MISCELLANEOUSMATHEMATICALSYMBOLS-B|MISCELLANEOUSSYMBOLS|"
        "MISCELLANEOUSSYMBOLSANDARROWS|MISCELLANEOUSSYMBOLSANDPICTOGRAPHS|"
        "MISCELLANEOUSTECHNICAL|MISCELLANEOUS_MATHEMATICAL_SYMBOLS_A|"
        "MISCELLANEOUS_MATHEMATICAL_SYMBOLS_B|MISCELLANEOUS_SYMBOLS|"
        "MISCELLANEOUS_SYMBOLS_AND_ARROWS|MISCELLANEOUS_SYMBOLS_AND_PICTOGRAPHS|"
        "MISCELLANEOUS_TECHNICAL|MODI|MODIFIER TONE LETTERS|MODIFIERTONELETTERS|"
        "MODIFIER_TONE_LETTERS|MONGOLIAN|MONGOLIAN SUPPLEMENT|MONGOLIANSUPPLEMENT|"
        "MONGOLIAN_SUPPLEMENT|MRO|MULTANI|MUSICAL SYMBOLS|MUSICALSYMBOLS|"
        "MUSICAL_SYMBOLS|MYANMAR|MYANMAR EXTENDED-A|MYANMAR EXTENDED-B|"
        "MYANMAR EXTENDED-C|MYANMAREXTENDED-A|MYANMAREXTENDED-B|MYANMAREXTENDED-C|"
        "MYANMAR_EXTENDED_A|MYANMAR_EXTENDED_B|MYANMAR_EXTENDED_C|NABATAEAN|"
        "NAG MUNDARI|NAGMUNDARI|NAG_MUNDARI|NANDINAGARI|NEW TAI LUE|NEWA|NEWTAILUE|"
        "NEW_TAI_LUE|NKO|NUMBER FORMS|NUMBERFORMS|NUMBER_FORMS|NUSHU|"
        "NYIAKENG PUACHUE HMONG|NYIAKENGPUACHUEHMONG|NYIAKENG_PUACHUE_HMONG|OGHAM|"
        "OL CHIKI|OL ONAL|OLCHIKI|OLD HUNGARIAN|OLD ITALIC|OLD NORTH ARABIAN|"
        "OLD PERMIC|OLD PERSIAN|OLD SOGDIAN|OLD SOUTH ARABIAN|OLD TURKIC|OLD UYGHUR|"
        "OLDHUNGARIAN|OLDITALIC|OLDNORTHARABIAN|OLDPERMIC|OLDPERSIAN|OLDSOGDIAN|"
        "OLDSOUTHARABIAN|OLDTURKIC|OLDUYGHUR|OLD_HUNGARIAN|OLD_ITALIC|"
        "OLD_NORTH_ARABIAN|OLD_PERMIC|OLD_PERSIAN|OLD_SOGDIAN|OLD_SOUTH_ARABIAN|"
        "OLD_TURKIC|OLD_UYGHUR|OLONAL|OL_CHIKI|OL_ONAL|OPTICAL CHARACTER RECOGNITION|"
        "OPTICALCHARACTERRECOGNITION|OPTICAL_CHARACTER_RECOGNITION|ORIYA|"
        "ORNAMENTAL DINGBATS|ORNAMENTALDINGBATS|ORNAMENTAL_DINGBATS|OSAGE|OSMANYA|"
        "OTTOMAN SIYAQ NUMBERS|OTTOMANSIYAQNUMBERS|OTTOMAN_SIYAQ_NUMBERS|PAHAWH HMONG|"
        "PAHAWHHMONG|PAHAWH_HMONG|PALMYRENE|PAU CIN HAU|PAUCINHAU|PAU_CIN_HAU|PHAGS-PA|"
        "PHAGS_PA|PHAISTOS DISC|PHAISTOSDISC|PHAISTOS_DISC|PHOENICIAN|"
        "PHONETIC EXTENSIONS|PHONETIC EXTENSIONS SUPPLEMENT|PHONETICEXTENSIONS|"
        "PHONETICEXTENSIONSSUPPLEMENT|PHONETIC_EXTENSIONS|"
        "PHONETIC_EXTENSIONS_SUPPLEMENT|PLAYING CARDS|PLAYINGCARDS|PLAYING_CARDS|"
        "PRIVATE USE AREA|PRIVATEUSEAREA|PRIVATE_USE_AREA|PSALTER PAHLAVI|"
        "PSALTERPAHLAVI|PSALTER_PAHLAVI|REJANG|RUMI NUMERAL SYMBOLS|RUMINUMERALSYMBOLS|"
        "RUMI_NUMERAL_SYMBOLS|RUNIC|SAMARITAN|SAURASHTRA|SHARADA|SHAVIAN|"
        "SHORTHAND FORMAT CONTROLS|SHORTHANDFORMATCONTROLS|SHORTHAND_FORMAT_CONTROLS|"
        "SIDDHAM|SINHALA|SINHALA ARCHAIC NUMBERS|SINHALAARCHAICNUMBERS|"
        "SINHALA_ARCHAIC_NUMBERS|SMALL FORM VARIANTS|SMALL KANA EXTENSION|"
        "SMALLFORMVARIANTS|SMALLKANAEXTENSION|SMALL_FORM_VARIANTS|SMALL_KANA_EXTENSION|"
        "SOGDIAN|SORA SOMPENG|SORASOMPENG|SORA_SOMPENG|SOYOMBO|"
        "SPACING MODIFIER LETTERS|SPACINGMODIFIERLETTERS|SPACING_MODIFIER_LETTERS|"
        "SPECIALS|SUNDANESE|SUNDANESE SUPPLEMENT|SUNDANESESUPPLEMENT|"
        "SUNDANESE_SUPPLEMENT|SUNUWAR|SUPERSCRIPTS AND SUBSCRIPTS|"
        "SUPERSCRIPTSANDSUBSCRIPTS|SUPERSCRIPTS_AND_SUBSCRIPTS|SUPPLEMENTAL ARROWS-A|"
        "SUPPLEMENTAL ARROWS-B|SUPPLEMENTAL ARROWS-C|"
        "SUPPLEMENTAL MATHEMATICAL OPERATORS|SUPPLEMENTAL PUNCTUATION|"
        "SUPPLEMENTAL SYMBOLS AND PICTOGRAPHS|SUPPLEMENTALARROWS-A|"
        "SUPPLEMENTALARROWS-B|SUPPLEMENTALARROWS-C|SUPPLEMENTALMATHEMATICALOPERATORS|"
        "SUPPLEMENTALPUNCTUATION|SUPPLEMENTALSYMBOLSANDPICTOGRAPHS|"
        "SUPPLEMENTAL_ARROWS_A|SUPPLEMENTAL_ARROWS_B|SUPPLEMENTAL_ARROWS_C|"
        "SUPPLEMENTAL_MATHEMATICAL_OPERATORS|SUPPLEMENTAL_PUNCTUATION|"
        "SUPPLEMENTAL_SYMBOLS_AND_PICTOGRAPHS|SUPPLEMENTARY PRIVATE USE AREA-A|"
        "SUPPLEMENTARY PRIVATE USE AREA-B|SUPPLEMENTARYPRIVATEUSEAREA-A|"
        "SUPPLEMENTARYPRIVATEUSEAREA-B|SUPPLEMENTARY_PRIVATE_USE_AREA_A|"
        "SUPPLEMENTARY_PRIVATE_USE_AREA_B|SURROGATES_AREA|SUTTON SIGNWRITING|"
        "SUTTONSIGNWRITING|SUTTON_SIGNWRITING|SYLOTI NAGRI|SYLOTINAGRI|SYLOTI_NAGRI|"
        "SYMBOLS AND PICTOGRAPHS EXTENDED-A|SYMBOLS FOR LEGACY COMPUTING|"
        "SYMBOLS FOR LEGACY COMPUTING SUPPLEMENT|SYMBOLSANDPICTOGRAPHSEXTENDED-A|"
        "SYMBOLSFORLEGACYCOMPUTING|SYMBOLSFORLEGACYCOMPUTINGSUPPLEMENT|"
        "SYMBOLS_AND_PICTOGRAPHS_EXTENDED_A|SYMBOLS_FOR_LEGACY_COMPUTING|"
        "SYMBOLS_FOR_LEGACY_COMPUTING_SUPPLEMENT|SYRIAC|SYRIAC SUPPLEMENT|"
        "SYRIACSUPPLEMENT|SYRIAC_SUPPLEMENT|TAGALOG|TAGBANWA|TAGS|TAI LE|TAI THAM|"
        "TAI VIET|TAI XUAN JING SYMBOLS|TAILE|TAITHAM|TAIVIET|TAIXUANJINGSYMBOLS|"
        "TAI_LE|TAI_THAM|TAI_VIET|TAI_XUAN_JING_SYMBOLS|TAKRI|TAMIL|TAMIL SUPPLEMENT|"
        "TAMILSUPPLEMENT|TAMIL_SUPPLEMENT|TANGSA|TANGUT|TANGUT COMPONENTS|"
        "TANGUT SUPPLEMENT|TANGUTCOMPONENTS|TANGUTSUPPLEMENT|TANGUT_COMPONENTS|"
        "TANGUT_SUPPLEMENT|TELUGU|THAANA|THAI|TIBETAN|TIFINAGH|TIRHUTA|TODHRI|TOTO|"
        "TRANSPORT AND MAP SYMBOLS|TRANSPORTANDMAPSYMBOLS|TRANSPORT_AND_MAP_SYMBOLS|"
        "TULU-TIGALARI|TULU_TIGALARI|UGARITIC|UNIFIED CANADIAN ABORIGINAL SYLLABICS|"
        "UNIFIED CANADIAN ABORIGINAL SYLLABICS EXTENDED|"
        "UNIFIED CANADIAN ABORIGINAL SYLLABICS EXTENDED-A|"
        "UNIFIEDCANADIANABORIGINALSYLLABICS|UNIFIEDCANADIANABORIGINALSYLLABICSEXTENDED|"
        "UNIFIEDCANADIANABORIGINALSYLLABICSEXTENDED-A|"
        "UNIFIED_CANADIAN_ABORIGINAL_SYLLABICS|"
        "UNIFIED_CANADIAN_ABORIGINAL_SYLLABICS_EXTENDED|"
        "UNIFIED_CANADIAN_ABORIGINAL_SYLLABICS_EXTENDED_A|VAI|VARIATION SELECTORS|"
        "VARIATION SELECTORS SUPPLEMENT|VARIATIONSELECTORS|"
        "VARIATIONSELECTORSSUPPLEMENT|VARIATION_SELECTORS|"
        "VARIATION_SELECTORS_SUPPLEMENT|VEDIC EXTENSIONS|VEDICEXTENSIONS|"
        "VEDIC_EXTENSIONS|VERTICAL FORMS|VERTICALFORMS|VERTICAL_FORMS|VITHKUQI|WANCHO|"
        "WARANG CITI|WARANGCITI|WARANG_CITI|YEZIDI|YI RADICALS|YI SYLLABLES|"
        "YIJING HEXAGRAM SYMBOLS|YIJINGHEXAGRAMSYMBOLS|YIJING_HEXAGRAM_SYMBOLS|"
        "YIRADICALS|YISYLLABLES|YI_RADICALS|YI_SYLLABLES|ZANABAZAR SQUARE|"
        "ZANABAZARSQUARE|ZANABAZAR_SQUARE|ZNAMENNY MUSICAL NOTATION|"
        "ZNAMENNYMUSICALNOTATION|ZNAMENNY_MUSICAL_NOTATION"
    ).split("|")
)
# `Character.UnicodeScript.forName`: the enum names and their aliases (JDK 25).
_SCRIPTS = frozenset(
    (
        "ADLAM|ADLM|AGHB|AHOM|ANATOLIAN_HIEROGLYPHS|ARAB|ARABIC|ARMENIAN|ARMI|ARMN|"
        "AVESTAN|AVST|BALI|BALINESE|BAMU|BAMUM|BASS|BASSA_VAH|BATAK|BATK|BENG|BENGALI|"
        "BHAIKSUKI|BHKS|BOPO|BOPOMOFO|BRAH|BRAHMI|BRAI|BRAILLE|BUGI|BUGINESE|BUHD|"
        "BUHID|CAKM|CANADIAN_ABORIGINAL|CANS|CARI|CARIAN|CAUCASIAN_ALBANIAN|CHAKMA|"
        "CHAM|CHER|CHEROKEE|CHORASMIAN|CHRS|COMMON|COPT|COPTIC|CPMN|CPRT|CUNEIFORM|"
        "CYPRIOT|CYPRO_MINOAN|CYRILLIC|CYRL|DESERET|DEVA|DEVANAGARI|DIAK|DIVES_AKURU|"
        "DOGR|DOGRA|DSRT|DUPL|DUPLOYAN|EGYP|EGYPTIAN_HIEROGLYPHS|ELBA|ELBASAN|ELYM|"
        "ELYMAIC|ETHI|ETHIOPIC|GARA|GARAY|GEOR|GEORGIAN|GLAG|GLAGOLITIC|GONG|GONM|GOTH|"
        "GOTHIC|GRAN|GRANTHA|GREEK|GREK|GUJARATI|GUJR|GUKH|GUNJALA_GONDI|GURMUKHI|GURU|"
        "GURUNG_KHEMA|HAN|HANG|HANGUL|HANI|HANIFI_ROHINGYA|HANO|HANUNOO|HATR|HATRAN|"
        "HEBR|HEBREW|HIRA|HIRAGANA|HLUW|HMNG|HMNP|HUNG|IMPERIAL_ARAMAIC|INHERITED|"
        "INSCRIPTIONAL_PAHLAVI|INSCRIPTIONAL_PARTHIAN|ITAL|JAVA|JAVANESE|KAITHI|KALI|"
        "KANA|KANNADA|KATAKANA|KAWI|KAYAH_LI|KHAR|KHAROSHTHI|KHITAN_SMALL_SCRIPT|KHMER|"
        "KHMR|KHOJ|KHOJKI|KHUDAWADI|KIRAT_RAI|KITS|KNDA|KRAI|KTHI|LANA|LAO|LAOO|LATIN|"
        "LATN|LEPC|LEPCHA|LIMB|LIMBU|LINA|LINB|LINEAR_A|LINEAR_B|LISU|LYCI|LYCIAN|LYDI|"
        "LYDIAN|MAHAJANI|MAHJ|MAKA|MAKASAR|MALAYALAM|MAND|MANDAIC|MANI|MANICHAEAN|MARC|"
        "MARCHEN|MASARAM_GONDI|MEDEFAIDRIN|MEDF|MEETEI_MAYEK|MEND|MENDE_KIKAKUI|MERC|"
        "MERO|MEROITIC_CURSIVE|MEROITIC_HIEROGLYPHS|MIAO|MLYM|MODI|MONG|MONGOLIAN|MRO|"
        "MROO|MTEI|MULT|MULTANI|MYANMAR|MYMR|NABATAEAN|NAGM|NAG_MUNDARI|NAND|"
        "NANDINAGARI|NARB|NBAT|NEWA|NEW_TAI_LUE|NKO|NKOO|NSHU|NUSHU|"
        "NYIAKENG_PUACHUE_HMONG|OGAM|OGHAM|OLCK|OLD_HUNGARIAN|OLD_ITALIC|"
        "OLD_NORTH_ARABIAN|OLD_PERMIC|OLD_PERSIAN|OLD_SOGDIAN|OLD_SOUTH_ARABIAN|"
        "OLD_TURKIC|OLD_UYGHUR|OL_CHIKI|OL_ONAL|ONAO|ORIYA|ORKH|ORYA|OSAGE|OSGE|OSMA|"
        "OSMANYA|OUGR|PAHAWH_HMONG|PALM|PALMYRENE|PAUC|PAU_CIN_HAU|PERM|PHAG|PHAGS_PA|"
        "PHLI|PHLP|PHNX|PHOENICIAN|PLRD|PRTI|PSALTER_PAHLAVI|REJANG|RJNG|ROHG|RUNIC|"
        "RUNR|SAMARITAN|SAMR|SARB|SAUR|SAURASHTRA|SGNW|SHARADA|SHAVIAN|SHAW|SHRD|SIDD|"
        "SIDDHAM|SIGNWRITING|SIND|SINH|SINHALA|SOGD|SOGDIAN|SOGO|SORA|SORA_SOMPENG|"
        "SOYO|SOYOMBO|SUND|SUNDANESE|SUNU|SUNUWAR|SYLO|SYLOTI_NAGRI|SYRC|SYRIAC|"
        "TAGALOG|TAGB|TAGBANWA|TAI_LE|TAI_THAM|TAI_VIET|TAKR|TAKRI|TALE|TALU|TAMIL|"
        "TAML|TANG|TANGSA|TANGUT|TAVT|TELU|TELUGU|TFNG|TGLG|THAA|THAANA|THAI|TIBETAN|"
        "TIBT|TIFINAGH|TIRH|TIRHUTA|TNSA|TODHRI|TODR|TOTO|TULU_TIGALARI|TUTG|UGAR|"
        "UGARITIC|UNKNOWN|VAI|VAII|VITH|VITHKUQI|WANCHO|WARA|WARANG_CITI|WCHO|XPEO|"
        "XSUX|YEZI|YEZIDI|YI|YIII|ZANABAZAR_SQUARE|ZANB|ZINH|ZYYY|ZZZZ"
    ).split("|")
)
# The binary properties of `CharPredicates.getUnicodePredicate` (JDK 25).
_BINARY_PROPERTIES = frozenset(
    (
        "ALPHABETIC|ASSIGNED|CONTROL|EMOJI|EMOJI_PRESENTATION|EMOJI_MODIFIER|"
        "EMOJI_MODIFIER_BASE|EMOJI_COMPONENT|EXTENDED_PICTOGRAPHIC|HEXDIGIT|HEX_DIGIT|"
        "IDEOGRAPHIC|JOINCONTROL|JOIN_CONTROL|LETTER|LOWERCASE|NONCHARACTERCODEPOINT|"
        "NONCHARACTER_CODE_POINT|TITLECASE|PUNCTUATION|UPPERCASE|WHITESPACE|"
        "WHITE_SPACE|WORD"
    ).split("|")
)
# `bitsOrSingle` makes a predicate, not bits, for these under Unicode case folding.
_UNICODE_FOLDS = frozenset((0xFF, 0xB5, 0x49, 0x69, 0x53, 0x73, 0x4B, 0x6B, 0xC5, 0xE5))
# Python derives these names from the code point; Java's `CharacterName` has none.
_DERIVED_NAMES = ("CJK UNIFIED IDEOGRAPH-", "HANGUL SYLLABLE ", "TANGUT IDEOGRAPH-")


class _Refused(Exception):
    def __init__(self, description, index):
        super().__init__(description)
        self.description = description
        self.index = index


class _Undecided(Exception):
    """The parser cannot decide as Java does; the pattern gets no verdict."""


def _int32(value):
    return (value + 0x80000000) % 0x100000000 - 0x80000000


def _is_digit(c):
    return 48 <= c <= 57


def _is_alpha(c):
    return 65 <= c <= 90 or 97 <= c <= 122


# Python knows every character name of JDK 25 from Unicode 16.0 (Python 3.14).
_KNOWS_NAMES = tuple(map(int, unicodedata.unidata_version.split("."))) >= (16, 0)


def _java_trim(text):
    """`String.trim`: without the characters up to U+0020 at both ends."""
    start, end = 0, len(text)
    while start < end and text[start] <= " ":
        start += 1
    while end > start and text[end - 1] <= " ":
        end -= 1
    return text[start:end]


def _java_name(cp):
    """The `CharacterName` entry of `cp`, or None."""
    name = unicodedata.name(chr(cp), None)
    if name is None or name.startswith(_DERIVED_NAMES):
        return None
    return name


def _code_point_of(name):
    """`Character.codePointOf(name)`, or None where Java throws."""
    name = _java_trim(name).upper()
    try:
        found = unicodedata.lookup(name)
    except KeyError:
        found = ""
    if len(found) == 1 and _java_name(ord(found)) == name:
        return ord(found)
    # The block form, `<BLOCK NAME> <HEX>`, of a code point without a name.
    form = re.fullmatch(r"(.+) ([0-9A-F]+)", name)
    if form and len(form[2]) <= 6:
        cp = int(form[2], 16)
        if (
            cp <= 0x10FFFF
            and "%X" % cp == form[2]
            and _java_name(cp) is None
            and unicodedata.category(chr(cp)) != "Cn"
            and form[1].replace(" ", "_") in _BLOCKS
        ):
            return cp
    return None


def _is_script(name):
    """Whether `Character.UnicodeScript.forName` knows `name`."""
    return name.upper() in _SCRIPTS


def _is_hex(c):
    return _is_digit(c) or 65 <= c <= 70 or 97 <= c <= 102


def _remove_qe_quoting(cps):
    """Java's `RemoveQEQuoting`: text between \\Q and \\E becomes literal."""
    n = len(cps)
    i = 0
    while i < n - 1:
        if cps[i] != 92:
            i += 1
        elif cps[i + 1] != 81:
            i += 2
        else:
            break
    if i >= n - 1:
        return cps
    out = cps[:i]
    i += 2
    in_quote = True
    begin_quote = True
    while i < n:
        c = cps[i]
        i += 1
        if c > 127 or _is_alpha(c):
            out.append(c)
        elif _is_digit(c):
            if begin_quote:
                out.extend((92, 120, 51))
            out.append(c)
        elif c != 92:
            if in_quote:
                out.append(92)
            out.append(c)
        elif in_quote:
            if i < n and cps[i] == 69:
                i += 1
                in_quote = False
            else:
                out.extend((92, 92))
        elif i < n and cps[i] == 81:
            i += 1
            in_quote = True
            begin_quote = True
            continue
        else:
            out.append(c)
            if i != n:
                out.append(cps[i])
                i += 1
        begin_quote = False
    return out


class _Pattern:
    def __init__(self, pattern):
        cps = [ord(c) for c in pattern]
        cps = _remove_qe_quoting(cps)
        self.length = len(cps)
        self.temp = cps + [0, 0]
        self.cursor = 0
        self.flags = 0
        self.groups = 1
        self.named = set()

    # -- cursor primitives, as in Pattern ------------------------------------
    def has(self, flag):
        return self.flags & flag

    def error(self, description):
        return _Refused(description, self.cursor - 1)

    def _line_separator(self, c):
        if self.has(_UNIX_LINES):
            return c == 10
        return c in (10, 13, 0x85) or (c | 1) == 0x2029

    def _peek_past_line(self):
        self.cursor += 1
        c = self.temp[self.cursor]
        while c != 0 and not self._line_separator(c):
            self.cursor += 1
            c = self.temp[self.cursor]
        if c == 0 and self.cursor > self.length:
            self.cursor = self.length
            c = self.temp[self.cursor]
        return c

    def _parse_past_line(self):
        c = self.temp[self.cursor]
        self.cursor += 1
        while c != 0 and not self._line_separator(c):
            c = self.temp[self.cursor]
            self.cursor += 1
        if c == 0 and self.cursor > self.length:
            self.cursor = self.length
            c = self.temp[self.cursor]
            self.cursor += 1
        return c

    def _peek_past_space(self, c):
        while c in _SPACE or c == 35:
            while c in _SPACE:
                self.cursor += 1
                c = self.temp[self.cursor]
            if c == 35:
                c = self._peek_past_line()
        return c

    def _parse_past_space(self, c):
        while c in _SPACE or c == 35:
            while c in _SPACE:
                c = self.temp[self.cursor]
                self.cursor += 1
            if c == 35:
                c = self._parse_past_line()
        return c

    def peek(self):
        c = self.temp[self.cursor]
        if self.has(_COMMENTS):
            c = self._peek_past_space(c)
        return c

    def read(self):
        c = self.temp[self.cursor]
        self.cursor += 1
        if self.has(_COMMENTS):
            c = self._parse_past_space(c)
        return c

    def next(self):
        self.cursor += 1
        c = self.temp[self.cursor]
        if self.has(_COMMENTS):
            c = self._peek_past_space(c)
        return c

    def next_escaped(self):
        self.cursor += 1
        return self.temp[self.cursor]

    def skip(self):
        c = self.temp[self.cursor + 1]
        self.cursor += 2
        return c

    def unread(self):
        self.cursor -= 1

    def accept(self, c, description):
        test = self.temp[self.cursor]
        self.cursor += 1
        if self.has(_COMMENTS):
            test = self._parse_past_space(test)
        if c != test:
            raise self.error(description)

    # -- grammar ---------------------------------------------------------------
    def compile(self):
        self.expr()
        if self.length != self.cursor:
            if self.peek() == 41:
                raise self.error("Unmatched closing ')'")
            if self.cursor == self.length + 1 and self.temp[self.length - 1] == 92:
                raise self.error("Unescaped trailing backslash")
            raise self.error("Unexpected internal error")

    def expr(self):
        while True:
            self.sequence()
            if self.peek() != 124:
                return
            self.next()

    def sequence(self):
        while True:
            c = self.peek()
            if c == 40:
                self.group0()
                continue
            if c == 91:
                self.clazz(True)
            elif c == 92:
                c = self.next_escaped()
                if c in (112, 80):
                    one_letter = True
                    if self.next() != 123:
                        self.unread()
                    else:
                        one_letter = False
                    self.family(one_letter)
                else:
                    self.unread()
                    self.atom()
            elif c in (94, 36, 46):
                self.next()
            elif c in (124, 41):
                return
            elif c in (63, 42, 43):
                self.next()
                raise self.error("Dangling meta character '%s'" % chr(c))
            elif c == 0 and self.cursor >= self.length:
                return
            else:
                self.atom()
            self.closure()

    def atom(self):
        first = 0
        prev = -1
        c = self.peek()
        while True:
            if c in (42, 43, 63, 123):
                if first > 1:
                    self.cursor = prev
                    first -= 1
                break
            if c in (36, 46, 94, 40, 91, 124, 41):
                break
            if c == 92:
                c = self.next_escaped()
                if c in (112, 80):
                    if first > 0:
                        self.unread()
                        break
                    one_letter = True
                    if self.next() != 123:
                        self.unread()
                    else:
                        one_letter = False
                    self.family(one_letter)
                    return
                self.unread()
                prev = self.cursor
                c = self.escape(False, first == 0, False)
                if c >= 0:
                    first += 1
                    c = self.peek()
                    continue
                if first == 0:
                    return
                self.cursor = prev
                break
            if c == 0 and self.cursor >= self.length:
                break
            prev = self.cursor
            first += 1
            c = self.next()

    def closure(self):
        c = self.peek()
        if c == 63:
            c = self.next()
            if c in (63, 43):
                self.next()
        elif c in (42, 43):
            c = self.next()
            if c in (63, 43):
                self.next()
        elif c == 123:
            c = self.skip()
            if not _is_digit(c):
                raise self.error("Illegal repetition")
            cmin = c - 48
            c = self.read()
            while _is_digit(c):
                cmin = cmin * 10 + (c - 48)
                if cmin > _MAX_REPS:
                    raise self.error("Illegal repetition range")
                c = self.read()
            cmax = cmin
            if c == 44:
                c = self.read()
                cmax = None
                if c != 125:
                    cmax = 0
                    while _is_digit(c):
                        cmax = cmax * 10 + (c - 48)
                        if cmax > _MAX_REPS:
                            raise self.error("Illegal repetition range")
                        c = self.read()
            if c != 125:
                raise self.error("Unclosed counted closure")
            if cmax is not None and cmax < cmin:
                raise self.error("Illegal repetition range")
            self.unread()
            if self.next() in (63, 43):
                self.next()

    def group0(self):
        saved = self.flags
        c = self.next()
        if c == 63:
            c = self.skip()
            if c in (58, 61, 33, 62):
                self.expr()
            elif c == 60:
                c = self.read()
                if c not in (61, 33):
                    name = self.groupname(c)
                    if name in self.named:
                        raise self.error(
                            "Named capturing group <%s> is already defined" % name
                        )
                    self.groups += 1
                    self.named.add(name)
                self.expr()
            elif c in (36, 64):
                raise self.error("Unknown group type")
            else:
                self.unread()
                self.add_flag()
                c = self.read()
                if c == 41:
                    return
                if c != 58:
                    raise self.error("Unknown inline modifier")
                self.expr()
        else:
            self.groups += 1
            self.expr()
        self.accept(41, "Unclosed group")
        self.flags = saved
        self.closure()

    def add_flag(self):
        c = self.peek()
        while True:
            flag = _FLAGS.get(chr(c)) if c < 128 else None
            if flag is not None:
                self.flags |= flag
            elif c == 45:
                c = self.next()
                self.sub_flag(c)
                return
            else:
                return
            c = self.next()

    def sub_flag(self, c):
        while True:
            flag = _FLAGS.get(chr(c)) if c < 128 else None
            if flag is None:
                return
            self.flags &= ~flag
            c = self.next()

    def groupname(self, c):
        name = []
        if not _is_alpha(c):
            raise self.error("capturing group name does not start with a Latin letter")
        while True:
            name.append(chr(c))
            c = self.read()
            if not (_is_alpha(c) or _is_digit(c)):
                break
        if c != 62:
            raise self.error("named capturing group is missing trailing '>'")
        return "".join(name)

    def escape(self, inclass, create, isrange):
        c = self.skip()
        ch = chr(c) if c < 128 else ""
        if ch == "0":
            return self.octal()
        if ch and ch in "123456789":
            if not inclass:
                return -1
        elif ch in ("A", "G", "R", "X", "Z", "z", "B"):
            if not inclass:
                return -1
        elif ch in ("D", "H", "S", "V", "W", "d", "h", "s", "w"):
            return -1
        elif ch == "N":
            return self.named_char()
        elif ch == "a":
            return 7
        elif ch == "b":
            if not inclass:
                if create and self.peek() == 123:
                    if self.skip() == 103:
                        if self.read() == 125:
                            return -1
                        raise self.error("Illegal/unsupported escape sequence")
                    self.unread()
                    self.unread()
                return -1
        elif ch == "c":
            if self.cursor < self.length:
                return self.read() ^ 64
            raise self.error("Illegal control escape sequence")
        elif ch == "e":
            return 27
        elif ch == "f":
            return 12
        elif ch == "k":
            if not inclass:
                if self.read() != 60:
                    raise self.error(
                        "\\k is not followed by '<' for named capturing group"
                    )
                name = self.groupname(self.read())
                if name not in self.named:
                    raise self.error("named capturing group <%s> does not exist" % name)
                return -1
        elif ch == "n":
            return 10
        elif ch == "r":
            return 13
        elif ch == "t":
            return 9
        elif ch == "u":
            return self.unicode()
        elif ch == "v":
            return 11 if isrange else -1
        elif ch == "x":
            return self.hexadecimal()
        elif not ch or not (_is_alpha(c)):
            return c
        raise self.error("Illegal/unsupported escape sequence")

    def octal(self):
        n = self.read()
        if 48 <= n <= 55:
            m = self.read()
            if 48 <= m <= 55:
                o = self.read()
                if 48 <= o <= 55 and 48 <= n <= 51:
                    return (n - 48) * 64 + (m - 48) * 8 + (o - 48)
                self.unread()
                return (n - 48) * 8 + (m - 48)
            self.unread()
            return n - 48
        raise self.error("Illegal octal escape sequence")

    def hexadecimal(self):
        n = self.read()
        if _is_hex(n):
            m = self.read()
            if _is_hex(m):
                return int(chr(n) + chr(m), 16)
        elif n == 123 and _is_hex(self.peek()):
            value = 0
            while True:
                n = self.read()
                if not _is_hex(n):
                    break
                value = value * 16 + int(chr(n), 16)
                if value > 0x10FFFF:
                    raise self.error("Hexadecimal codepoint is too big")
            if n != 125:
                raise self.error("Unclosed hexadecimal escape sequence")
            return value
        raise self.error("Illegal hexadecimal escape sequence")

    def _uxxxx(self):
        value = 0
        for _ in range(4):
            c = self.read()
            if not _is_hex(c):
                raise self.error("Illegal Unicode escape sequence")
            value = value * 16 + int(chr(c), 16)
        return value

    def unicode(self):
        value = self._uxxxx()
        if 0xD800 <= value <= 0xDBFF:
            saved = self.cursor
            if self.read() == 92 and self.read() == 117:
                low = self._uxxxx()
                if 0xDC00 <= low <= 0xDFFF:
                    return 0x10000 + ((value - 0xD800) << 10) + (low - 0xDC00)
            self.cursor = saved
        return value

    def named_char(self):
        if self.read() == 123:
            start = self.cursor
            while self.read() != 125:
                if self.cursor >= self.length:
                    raise self.error("Unclosed character name escape sequence")
            name = "".join(map(chr, self.temp[start : self.cursor - 1]))
            cp = _code_point_of(name)
            if cp is None:
                if not _KNOWS_NAMES:
                    raise _Undecided()
                raise self.error("Unknown character name [%s]" % name)
            return cp
        raise self.error("Illegal character name escape sequence")

    def family(self, one_letter):
        self.next()
        if one_letter:
            name = chr(self.temp[self.cursor])
            self.read()
        else:
            start = self.cursor
            self.temp[self.length] = 125
            while self.read() != 125:
                pass
            self.temp[self.length] = 0
            end = self.cursor
            if end > self.length:
                raise self.error("Unclosed character family")
            if start + 1 >= end:
                raise self.error("Empty character family")
            name = "".join(map(chr, self.temp[start : end - 1]))
        if "=" in name:
            key, value = name.split("=", 1)
            key = key.lower()
            if key in ("sc", "script"):
                known = _is_script(value)
            elif key in ("blk", "block"):
                known = value.upper() in _BLOCKS
            elif key in ("gc", "general_category"):
                known = value in _PROPERTIES
            else:
                known = False
            if not known:
                raise self.error(
                    "Unknown Unicode property {name=<%s>, value=<%s>}" % (key, value)
                )
            return
        if name.startswith("In"):
            known = name[2:].upper() in _BLOCKS
        elif name.startswith("Is"):
            short = name[2:]
            known = (
                short.upper() in _BINARY_PROPERTIES
                or short.upper() in _POSIX_UPPER
                or short in _PROPERTIES
                or _is_script(short)
            )
        else:
            known = name in _PROPERTIES or (
                self.has(_UNICODE_CHARACTER_CLASS) and name.upper() in _POSIX_UPPER
            )
        if not known:
            raise self.error("Unknown character property name {%s}" % name)

    def clazz(self, consume):
        # `prev` and `curr` say whether Java's predicates are set, as in `Pattern`.
        prev = curr = has_bits = False
        c = self.next()
        if c == 94 and self.temp[self.cursor - 1] == 91:
            c = self.next()
        while True:
            if c == 91:
                self.clazz(True)
                prev = curr = True
                c = self.peek()
                continue
            if c == 38:
                c = self.next()
                if c == 38:
                    c = self.next()
                    right = False
                    while c not in (93, 38):
                        if c != 91:
                            self.unread()
                            if has_bits:
                                curr = curr or not prev
                                prev, has_bits = True, False
                        self.clazz(c == 91)
                        right = True
                        c = self.peek()
                    if has_bits:
                        curr = curr or not prev
                        prev, has_bits = True, False
                    curr = curr or right
                    if not prev and not right:
                        raise self.error("Bad class syntax")
                    if prev and not curr:
                        raise self.error("Bad intersection syntax")
                    prev = True
                    continue
                self.unread()
            elif c == 0 and self.cursor >= self.length:
                raise self.error("Unclosed character class")
            elif c == 93 and (prev or has_bits):
                if consume:
                    self.next()
                return
            if self.range():
                prev = curr = True
            else:
                has_bits, curr = True, False
            c = self.peek()

    def range(self):
        """One class member. True when Java builds a predicate, False for bits."""
        c = self.peek()
        if c == 92:
            c = self.next_escaped()
            if c in (112, 80):
                one_letter = True
                if self.next() != 123:
                    self.unread()
                else:
                    one_letter = False
                self.family(one_letter)
                return True
            isrange = self.temp[self.cursor + 1] == 45
            self.unread()
            c = self.escape(True, True, isrange)
            if c == -1:
                return True
        else:
            self.next()
        if c >= 0:
            if self.peek() == 45:
                end = self.temp[self.cursor + 1]
                if end == 91:
                    return self.single(c)
                if end != 93:
                    self.next()
                    m = self.peek()
                    if m == 92:
                        m = self.escape(True, False, True)
                    else:
                        self.next()
                    if m < c:
                        raise self.error("Illegal character range")
                    return True
            return self.single(c)
        raise self.error("Unexpected character '%s'" % chr(c))

    def single(self, c):
        """`bitsOrSingle`: True when Java makes a predicate, not bits, for `c`."""
        folds = self.has(0x02) and self.has(0x40) and c in _UNICODE_FOLDS
        return c >= 256 or folds


def problem(pattern):
    """The first line of Java's message for `pattern`, or None when it compiles.

    None also means no verdict: the parser cannot decide as Java does."""
    parser = _Pattern(pattern)
    try:
        parser.compile()
    except _Refused as refused:
        message = refused.description
        if refused.index >= 0:
            message = "%s near index %d" % (message, refused.index)
        line = message.split("\n")[0]
        return line[:-1] if line != message and line.endswith("\r") else line
    except (IndexError, _Undecided):
        return None
    return None
