"""Hindi and Urdu in Roman letters ("Hinglish"), for captions: चूना जो उसको -> chuna jo usko, یار کیا ہے -> yaar kya hai.

Not a translation: the same word, spelled the way people type it in chats. The AI does this when it tidies a
clip's captions (brain.fix_caption_words); this is the fallback for when there is no AI, or it missed a word, so
that a caption is never drawn in Hindi or Urdu script. Urdu is written without most vowels, so common words come
from a list and the rest are spelled from their letters: readable, if not always how a person would type it."""
import re

_VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e", "ऐ": "ai",
           "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e"}
_SIGNS = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o",
          "ौ": "au", "ॉ": "o", "ॅ": "e"}
_CONS = {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh",
         "ञ": "n", "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh",
         "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v",
         "श": "sh", "ष": "sh", "स": "s", "ह": "h", "ळ": "l",
         "क़": "q", "ख़": "kh", "ग़": "gh", "ज़": "z", "फ़": "f", "ड़": "r", "ढ़": "rh", "य़": "y"}
_NUKTA = {"क": "q", "ख": "kh", "ग": "gh", "ज": "z", "फ": "f", "ड": "r", "ढ": "rh", "य": "y"}
_VIRAMA, _NUKTA_SIGN = "्", "़"
_NASAL = {"ं": "n", "ँ": "n"}
_OTHER = {"ः": "h", "।": ".", "॥": ".", "ॐ": "om", **{chr(0x0966 + i): str(i) for i in range(10)}}
# Common words whose everyday spelling the rules below would not guess.
_HINDI_COMMON = {"में": "mein", "मैं": "main", "हैं": "hain", "है": "hai", "नहीं": "nahi", "नही": "nahi", "हूँ": "hoon",
                 "हूं": "hoon", "क्या": "kya", "क्यों": "kyun", "क्यूँ": "kyun", "यह": "yeh", "वह": "woh", "और": "aur",
                 "भी": "bhi", "ही": "hi", "की": "ki", "जी": "ji", "तो": "to", "हाँ": "haan", "हां": "haan", "यहाँ": "yahan",
                 "यहां": "yahan", "वहाँ": "wahan", "वहां": "wahan", "कहाँ": "kahan", "कहां": "kahan", "थी": "thi",
                 "रही": "rahi", "कभी": "kabhi", "अभी": "abhi", "सभी": "sabhi", "भाई": "bhai", "मुझे": "mujhe",
                 "तुम": "tum", "हम": "hum", "आप": "aap", "बहुत": "bahut", "अच्छा": "accha", "ठीक": "theek", "लिए": "liye"}

# English loanwords frequently spoken in Hindi/Urdu and transcribed in Devanagari.
# Spelled in standard English so captions read as clean mixed English + Hinglish.
_LOANWORDS = {
    # Infrastructure, real estate & places
    "सप्लाई": "supply", "सप्लाय": "supply",
    "रोड": "road", "रोड्स": "roads",
    "कनेक्टिविटी": "connectivity",
    "डेवलपमेंट": "development", "डिवेलपमेंट": "development",
    "एनवायरमेंट": "environment", "एनवायरनमेंट": "environment",
    "लोकेशन": "location",
    "साइट": "site", "साइट्स": "sites",
    "प्लॉट": "plot", "प्लॉट्स": "plots",
    "कॉलोनी": "colony", "कालोनी": "colony",
    "सोसाइटी": "society", "सोसायटी": "society",
    "कैपेसिटी": "capacity",
    "टैंक": "tank", "टैंक्स": "tanks",
    "वाटर": "water",
    "लाइन": "line", "लाइंस": "lines", "लाइनें": "lines",
    "इलेक्ट्रिसिटी": "electricity", "इलेक्ट्रिफाइड": "electrified",
    "पोल": "pole", "पोल्स": "poles",
    "केबल": "cable", "केबल्स": "cables", "केबलिंग": "cabling",
    "टेप": "tape", "टेपिंग": "taping",
    "ट्रांसफार्मर": "transformer", "ट्रांसफॉर्मर": "transformer",
    "रजिस्ट्री": "registry",
    "मीटर": "meter", "मीटर्स": "meters",
    "गवर्नमेंट": "government",
    "साइज़": "size", "साइज": "size", "साइजेस": "sizes", "साइज़ेस": "sizes",
    "एरिया": "area",
    "प्रोजेक्ट": "project", "प्रोजेक्ट्स": "projects",
    "ऑलमोस्ट": "almost",
    "पर्सेंट": "percent", "पर्सेंटेज": "percentage", "परसेंट": "percent", "परसेंटेज": "percentage",
    "इंटरलाकिंग": "interlocking", "इंटरलॉकिंग": "interlocking",
    "पर्सनल": "personal",
    "प्रॉपर": "proper", "प्रॉपर्ली": "properly",
    "सिक्योरिटी": "security", "गार्ड": "guard", "गार्ड्स": "guards",
    "सीसीटीवी": "CCTV",
    "बाउंड्री": "boundary",
    "मार्केट": "market", "हॉस्पिटल": "hospital", "स्कूल": "school", "कॉलेज": "college", "यूनिवर्सिटी": "university", "पार्क": "park",
    "ऑफिस": "office", "शॉप": "shop", "शोरूम": "showroom", "बैंक": "bank", "लोन": "loan",
    "पेमेंट": "payment", "कैश": "cash", "रेट": "rate", "प्राइस": "price", "डिस्काउंट": "discount", "ऑफर": "offer",
    "इन्वेस्टमेंट": "investment", "इन्वेस्टर": "investor", "प्रॉफिट": "profit", "कस्टमर": "customer", "क्लाइंट": "client", "विजिट": "visit",
    "नंबर": "number", "कांटेक्ट": "contact", "कॉल": "call", "मैसेज": "message", "व्हाट्सएप": "WhatsApp",

    # Social Media, Streaming & YouTube
    "सब्सक्राइब": "subscribe", "सब्सक्राइबर": "subscriber", "सब्सक्राइबर्स": "subscribers",
    "लाइक": "like", "लाइक्स": "likes",
    "शेयर": "share",
    "कमेंट": "comment", "कमेंट्स": "comments",
    "फॉलो": "follow", "फॉलोअर्स": "followers", "फॉलोवर": "follower",
    "वीडियो": "video", "वीडियोज": "videos", "वीडियोज़": "videos",
    "चैनल": "channel", "चैनल्स": "channels",
    "लाइव": "live", "स्ट्रीम": "stream", "स्ट्रीमर": "streamer", "स्ट्रीमिंग": "streaming",
    "ऑडियंस": "audience", "व्यूज": "views", "व्यूअर्स": "viewers",
    "पोस्ट": "post", "स्टोरी": "story", "रील्स": "reels", "शॉर्ट्स": "shorts", "क्लिप": "clip", "क्लिप्स": "clips",
    "लिंक": "link", "डिस्क्रिप्शन": "description", "प्रोफाइल": "profile", "अकाउंट": "account",
    "नोटिफिकेशन": "notification", "बेल": "bell", "आइकन": "icon",

    # Gaming & Tech
    "गेम": "game", "गेमिंग": "gaming", "गेमप्ले": "gameplay", "गेम्स": "games", "गेमर": "gamer",
    "प्ले": "play", "प्लेयर": "player", "प्लेयर्स": "players",
    "टीम": "team", "टीममेट": "teammate", "टीममेट्स": "teammates", "स्क्वाड": "squad",
    "मैच": "match", "मैचेस": "matches", "राउंड": "round", "राउंड्स": "rounds", "लेवल": "level",
    "क्लच": "clutch", "किल": "kill", "किल्स": "kills", "शॉट": "shot", "हेडशॉट": "headshot",
    "लॉबी": "lobby", "सर्वर": "server", "पिंग": "ping", "लैग": "lag", "हैक": "hack", "हैकर": "hacker",
    "अपडेट": "update", "डाउनलोड": "download", "ऑनलाइन": "online", "ऑफलाइन": "offline",
    "मोबाइल": "mobile", "फोन": "phone", "कंप्यूटर": "computer", "लैपटॉप": "laptop", "स्क्रीन": "screen",
    "कैमरा": "camera", "माइक": "mic", "हेडफोन": "headphones", "हेडफोन्स": "headphones",

    # Conversational & common expressions
    "गाइज": "guys", "गाइस": "guys", "गाइज़": "guys", "ब्रो": "bro", "सर": "sir", "मैम": "ma'am",
    "प्लीज": "please", "थैंक": "thank", "थैंक्स": "thanks", "वेलकम": "welcome", "सॉरी": "sorry",
    "ओके": "okay", "यस": "yes", "नो": "no", "बाय": "bye", "हेलो": "hello", "हाय": "hi",
    "वेट": "wait", "चेक": "check", "लुक": "look", "स्टार्ट": "start", "स्टॉप": "stop",
    "बेस्ट": "best", "बैड": "bad", "गुड": "good", "नाइस": "nice", "सुपर": "super", "ग्रेट": "great", "क्रेजी": "crazy", "कूल": "cool", "ऑसम": "awesome",
    "इजी": "easy", "हार्ड": "hard", "फास्ट": "fast", "स्लो": "slow", "बिग": "big", "स्मॉल": "small",
    "रियल": "real", "फेक": "fake", "राइट": "right", "रॉन्ग": "wrong",
    "सिंपल": "simple", "नॉर्मल": "normal", "इंपॉर्टेंट": "important", "इम्पॉर्टेंट": "important",
    "टाइम": "time", "डे": "day", "नाइट": "night", "मॉर्निंग": "morning", "इवनिंग": "evening",
    "कार": "car", "बाइक": "bike", "बस": "bus", "ट्रेन": "train", "मेट्रो": "metro",
    "फैमिली": "family", "फ्रेंड": "friend", "फ्रेंड्स": "friends",
    "होटल": "hotel", "रूम": "room", "फूड": "food", "पार्टी": "party", "म्यूजिक": "music"
}
_WORDS = {**_HINDI_COMMON, **_LOANWORDS}
DEVANAGARI = re.compile(r"[ऀ-ॿ]")
BENGALI = re.compile(r"[\u0980-\u09FF]")
OTHER_SCRIPT = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")   # Urdu / Arabic letters


def _bengali_to_devanagari(text: str) -> str:
    res = []
    for ch in text:
        o = ord(ch)
        if 0x0980 <= o <= 0x09FF:
            if o == 0x09F0:
                res.append("र")
            elif o == 0x09F1:
                res.append("व")
            elif o == 0x09CE:
                res.append("त्")
            elif 0x0900 <= o - 0x80 <= 0x097F:
                res.append(chr(o - 0x80))
            else:
                res.append(ch)
        else:
            res.append(ch)
    return "".join(res)


def has_hindi(text: str) -> bool:
    return bool(DEVANAGARI.search(text or "") or BENGALI.search(text or ""))


def needs_roman(text: str) -> bool:
    """Is this in a script that captions should show in Roman letters instead (Hindi, Urdu, Arabic, Russian-style,
    Greek or Korean letters)?"""
    text = text or ""
    return bool(DEVANAGARI.search(text) or BENGALI.search(text) or OTHER_SCRIPT.search(text) or MORE_SCRIPTS.search(text))


def drawable(text: str) -> bool:
    """Can the caption fonts draw this? They have Latin letters (with accents), digits and punctuation only."""
    return not any(ch.isalpha() and not (ord(ch) < 0x250 or 0x1E00 <= ord(ch) <= 0x1EFF) for ch in text or "")


# ---------------------------------------------------------------- Russian-style (Cyrillic), Greek and Korean letters
MORE_SCRIPTS = re.compile(r"[\u0370-\u03FF\u0400-\u04FF\uAC00-\uD7A3]")
_CYR = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y",
        "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
        "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
        "я": "ya", "є": "ye", "і": "i", "ї": "yi", "ґ": "g", "ў": "u", "ђ": "dj", "ј": "j", "љ": "lj", "њ": "nj",
        "ћ": "c", "џ": "dz", "ә": "a", "ғ": "g", "қ": "q", "ң": "n", "ө": "o", "ұ": "u", "ү": "u", "һ": "h"}
_GRK = {"α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th", "ι": "i", "κ": "k", "λ": "l",
        "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p", "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y", "φ": "f",
        "χ": "ch", "ψ": "ps", "ω": "o"}
_K_FIRST = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
_K_VOWEL = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu",
            "eu", "ui", "i"]
_K_LAST = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l", "m", "p", "p", "t", "t", "ng",
           "t", "t", "k", "t", "p", "t"]


def _letters(run: str) -> str:
    import unicodedata
    out = []
    for ch in unicodedata.normalize("NFD", run):
        if unicodedata.combining(ch):
            continue                                   # Greek accents
        low = ch.lower()
        r = _CYR.get(low, _GRK.get(low))
        if r is None:
            out.append(ch)
        else:
            out.append(r.capitalize() if ch != low and r else r)
    return "".join(out)


def _korean(run: str) -> str:
    out = []
    for ch in run:
        n = ord(ch) - 0xAC00
        out.append(_K_FIRST[n // 588] + _K_VOWEL[n % 588 // 28] + _K_LAST[n % 28] if 0 <= n < 11172 else ch)
    return "".join(out)


def _word(w: str) -> str:
    if w in _WORDS:
        return _WORDS[w]
    # One unit per letter: [roman consonant or vowel, its vowel or None (None = a consonant with no vowel), is_cons]
    units, i = [], 0
    while i < len(w):
        ch = w[i]
        nxt = w[i + 1] if i + 1 < len(w) else ""
        if ch in _CONS:
            c = _CONS[ch]
            if nxt == _NUKTA_SIGN:
                c = _NUKTA.get(ch, c)
                i += 1
                nxt = w[i + 1] if i + 1 < len(w) else ""
            if nxt == _VIRAMA:
                units.append([c, None, True, False])
                i += 2
                continue
            if nxt in _SIGNS:
                units.append([c, _SIGNS[nxt], True, False])
                i += 2
                continue
            units.append([c, "a", True, True])        # the vowel every Hindi letter carries unless told otherwise
        elif ch in _VOWELS:
            units.append(["", _VOWELS[ch], False, False])
        elif ch in _NASAL:
            if units:
                units[-1][1] = (units[-1][1] or "") + "n"
                units[-1][3] = False
                units[-1].append("nasal")
            else:
                units.append(["n", None, True, False])
        elif ch in _OTHER:
            units.append([_OTHER[ch], None, False, False])
        elif ch == _NUKTA_SIGN or ch == _VIRAMA:
            pass
        else:
            units.append([ch, None, False, False])
        i += 1
    letters = [u for u in units if u[2] or u[1]]
    # Hindi drops the carried "a" at the end of a word, and between a vowel and another vowel-carrying letter
    # (कहना -> kahna, not kahana). Looked at right to left, as speakers do.
    if len(letters) > 1 and letters[-1][3]:
        letters[-1][1] = None
    for k in range(len(letters) - 2, 0, -1):
        u, before, after = letters[k], letters[k - 1], letters[k + 1]
        if u[3] and before[1] and after[2] and after[1] and "nasal" not in before:
            u[1] = None
    # Long vowels the way people type them: doubled only before a consonant that ends a syllable (baat, kaam,
    # aaj), single elsewhere (hamara, pata, ki, tu).
    short = {"aa": "a", "ee": "i", "oo": "u"}
    for k, u in enumerate(letters):
        if u[1] in short:
            after = letters[k + 1] if k + 1 < len(letters) else None
            if after is None or after[1]:
                u[1] = short[u[1]]
    return "".join(u[0] + (u[1] or "") for u in units)


# ---------------------------------------------------------------- Urdu
_UR_WORDS = {"ہے": "hai", "ہیں": "hain", "ہوں": "hoon", "ہو": "ho", "ہی": "hi", "یہ": "ye", "وہ": "wo", "کیا": "kya",
             "کیوں": "kyun", "نہیں": "nahi", "نہ": "na", "میں": "main", "میرا": "mera", "میری": "meri", "میرے": "mere",
             "تو": "to", "کہ": "ke", "کی": "ki", "کا": "ka", "کے": "ke", "کو": "ko", "سے": "se", "پر": "par",
             "اور": "aur", "بھی": "bhi", "تھا": "tha", "تھی": "thi", "تھے": "the", "گیا": "gaya", "گئی": "gayi",
             "رہا": "raha", "رہی": "rahi", "رہے": "rahe", "بہت": "bahut", "اچھا": "achha", "یار": "yaar", "بھائی": "bhai",
             "آپ": "aap", "ہم": "hum", "تم": "tum", "مجھے": "mujhe", "تمہیں": "tumhein", "اب": "ab", "کر": "kar",
             "کرو": "karo", "کرنا": "karna", "دیکھو": "dekho", "چلو": "chalo", "ابھی": "abhi", "کبھی": "kabhi",
             "جی": "ji", "ہاں": "haan", "نا": "na", "اللہ": "Allah", "ٹھیک": "theek", "پتا": "pata", "پتہ": "pata",
             "لیکن": "lekin", "پھر": "phir", "سب": "sab", "کچھ": "kuch", "کون": "kaun", "کہاں": "kahan", "یہاں": "yahan",
             "وہاں": "wahan", "ایک": "ek", "دو": "do", "اپنا": "apna", "اپنی": "apni", "لوگ": "log", "بات": "baat",
             "کام": "kaam", "دن": "din", "آج": "aaj", "کل": "kal", "بس": "bas", "شکریہ": "shukriya", "واہ": "wah",
             "گھر": "ghar", "ہوا": "hua", "ہوئی": "hui", "ہوئے": "hue", "اس": "us", "ان": "un", "جو": "jo", "جب": "jab",
             "تب": "tab", "مگر": "magar", "پاکستان": "Pakistan", "زندگی": "zindagi", "پیسے": "paise", "روپے": "rupay"}
_UR_LOANWORDS = {
    "روڈ": "road", "ویڈیو": "video", "چینل": "channel", "گیم": "game", "میچ": "match",
    "ٹیم": "team", "سبسکرائب": "subscribe", "لائیک": "like", "شیئر": "share", "فالو": "follow",
    "کمنٹ": "comment", "آن لائن": "online", "موبائل": "mobile", "فون": "phone", "کمپیوٹر": "computer",
    "سکول": "school", "کالج": "college", "ہسپتال": "hospital", "پارک": "park", "کار": "car",
    "کلچ": "clutch", "لیول": "level", "ہوٹل": "hotel", "روم": "room", "پوسٹ": "post", "لائیو": "live"
}
_UR_WORDS = {**_UR_WORDS, **_UR_LOANWORDS}
_UR_CONS = {"ب": "b", "پ": "p", "ت": "t", "ٹ": "t", "ث": "s", "ج": "j", "چ": "ch", "ح": "h", "خ": "kh", "د": "d",
            "ڈ": "d", "ذ": "z", "ر": "r", "ڑ": "r", "ز": "z", "ژ": "zh", "س": "s", "ش": "sh", "ص": "s", "ض": "z",
            "ط": "t", "ظ": "z", "غ": "gh", "ف": "f", "ق": "q", "ک": "k", "ك": "k", "گ": "g", "ل": "l", "م": "m",
            "ن": "n", "ہ": "h", "ه": "h", "ة": "a", "ھ": "h"}
_UR_MARKS = {"َ": "a", "ِ": "i", "ُ": "u", "ّ": "", "ْ": "", "ً": "an", "ٰ": "a"}
_UR_DIGITS = {**{chr(0x06F0 + i): str(i) for i in range(10)}, **{chr(0x0660 + i): str(i) for i in range(10)}}


def _urdu_word(w: str) -> str:
    if w in _UR_WORDS:
        return _UR_WORDS[w]
    out, prev_cons = [], False
    for i, ch in enumerate(w):
        first, last = i == 0, i == len(w) - 1
        nxt = w[i + 1] if i + 1 < len(w) else ""
        if ch in _UR_MARKS:
            out.append(_UR_MARKS[ch])
            prev_cons = False
            continue
        if ch in _UR_DIGITS:
            out.append(_UR_DIGITS[ch])
            prev_cons = False
            continue
        if ch == "ھ":                     # do chashmi he: the "h" of bh, ph, kh, chh...
            out.append("h")
            continue
        if ch in "اآ":
            out.append("aa" if ch == "آ" else "a")
            prev_cons = False
            continue
        if ch in "وؤ":
            if first or not prev_cons:
                out.append("w" if nxt not in "اآ" else "w")
                prev_cons = True
            else:
                out.append("o")
                prev_cons = False
            continue
        if ch in "یيئى":
            if first:
                out.append("y")
                prev_cons = True
            else:
                out.append("i" if last else "ee" if prev_cons and nxt not in "اآوؤ" else "y")
                prev_cons = out[-1] == "y"
            continue
        if ch == "ے":
            out.append("e")
            prev_cons = False
            continue
        if ch in "ںن" and ch == "ں":
            out.append("n")
            continue
        if ch in "عء":
            continue
        c = _UR_CONS.get(ch)
        if c is None:
            out.append(ch)
            prev_cons = False
            continue
        if ch in "ہه" and last and prev_cons:
            out.append("a")               # a final ہ after a consonant is a vowel: کہ, بچہ -> bacha
            prev_cons = False
            continue
        if prev_cons and i == 1 or (prev_cons and i == 2 and w[1] == "ھ"):
            out.append("a")               # the short "a" Urdu does not write after a word's first letter: گھر -> ghar
        out.append(c)
        prev_cons = True
    s = "".join(out)
    if re.fullmatch(r"[a-z]{2,}", s) and not re.search(r"[aeiou]", s):
        s = s[:-1] + "a" + s[-1]          # no vowel at all: give it the one Urdu leaves out
    return s


_HINDI_RUN = r"[ऀ-ॿ]+"
_URDU_RUN = r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]+"


def to_roman(text: str) -> str:
    """Every Hindi and Urdu word in the text spelled in Roman letters. Anything else is left exactly as it is."""
    text = text or ""
    if BENGALI.search(text):
        text = _bengali_to_devanagari(text)
    if DEVANAGARI.search(text):
        text = re.sub(_HINDI_RUN, lambda m: _word(m.group(0)), text)
    if OTHER_SCRIPT.search(text):
        text = text.replace("،", ",").replace("؟", "?").replace("۔", ".")
        text = re.sub(_URDU_RUN, lambda m: _urdu_word(m.group(0)), text)
    if MORE_SCRIPTS.search(text):
        import unicodedata
        text = "".join(unicodedata.normalize("NFD", ch) if "\u0370" <= ch <= "\u04FF" or "\u1F00" <= ch <= "\u1FFF" else ch for ch in text)
        text = re.sub(r"[\uAC00-\uD7A3]+", lambda m: _korean(m.group(0)), text)
        text = unicodedata_nfc(re.sub(r"[\u0370-\u03FF\u0400-\u04FF\u0300-\u036F]+", lambda m: _letters(m.group(0)), text))
    return text


def unicodedata_nfc(s: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFC", s)


def words(ws: list) -> list:
    """Caption words ({'w', 's', 'e'}) with any Hindi or Urdu in Roman letters. Timings are untouched."""
    return [dict(w, w=to_roman(w["w"])) if needs_roman(w.get("w", "")) else w for w in ws]
