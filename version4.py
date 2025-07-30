import streamlit as st
import re
import zlib

# --------------------------
# 🧠 Encode PlantUML to URL
# --------------------------
def _generate_url(payload, server="http://www.plantuml.com/plantuml/png/"):
    """
    Compresses and encodes the PlantUML code into a URL-friendly format
    using zlib and a custom Base64-like alphabet.
    """
    def _zip_bytes(text):
        raw = text.encode("utf-8")
        comp = zlib.compress(raw)[2:-4]  # strip zlib header/footer
        return _to_custom_b64(comp)

    def _to_custom_b64(data):
        abc = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_'
        r = ''
        i = 0
        while i < len(data):
            b1 = data[i]
            b2 = data[i + 1] if i + 1 < len(data) else 0
            b3 = data[i + 2] if i + 2 < len(data) else 0
            r += abc[b1 >> 2]
            r += abc[((b1 & 0x3) << 4) | (b2 >> 4)]
            r += abc[((b2 & 0xF) << 2) | (b3 >> 6)]
            r += abc[b3 & 0x3F]
            i += 3
        return r

    return server + _zip_bytes(payload)

# --------------------------
# 🔄 Log Parser - Sequence
# --------------------------
def _build_seq(logs):
    """
    Parses logs to generate PlantUML sequence diagram.
    Uses pattern-to-action mapping via regex and lambdas.
    """
    patt_map = {
        "a": (re.compile(r".*Function (\w+) is called"), lambda f: f"X->{f}: call"),
        "b": (re.compile(r".*Function (\w+) is completed"), lambda f: f"{f}-->X: return"),
        "c": (re.compile(r".*Function (\w+) caused error"), lambda f: f"{f}-->X: error"),
        "d": (re.compile(r".*Retrying Function (\w+)"), lambda f: f"X->{f}: retry"),
        "e": (re.compile(r".*Function (\w+) is skipped"), lambda f: f"note right of X: Skipped {f}"),
    }

    res = ["@startuml", "participant X"]
    seen = set(["X"])

    for ln in logs:
        line = ln.strip()
        matched = False
        for _, (r, action) in patt_map.items():
            m = r.match(line)
            if m:
                fn = m.group(1)
                if fn not in seen:
                    res.append(f"participant {fn}")
                    seen.add(fn)
                res.append(action(fn))
                matched = True
                break
        if not matched:
            res.append(f"note right of X: {line}")
    res.append("@enduml")
    return "\n".join(res)

# --------------------------
# 🔄 Log Parser - Activity
# --------------------------
def _build_act(logs):
    """
    Parses logs to generate PlantUML activity diagram.
    """
    res = ["@startuml", "start"]
    for line in logs:
        line = line.strip()
        if "is called" in line:
            fn = re.search(r"Function (\w+)", line)
            if fn: res.append(f":Call {fn.group(1)};")
        elif "is completed" in line:
            fn = re.search(r"Function (\w+)", line)
            if fn: res.append(f":Complete {fn.group(1)};")
        elif "caused error" in line:
            fn = re.search(r"Function (\w+)", line)
            if fn: res.append(f"note right: {fn.group(1)} error")
        elif "is skipped" in line:
            fn = re.search(r"Function (\w+)", line)
            if fn: res.append(f"note right: {fn.group(1)} skipped")
        elif "Retrying Function" in line:
            fn = re.search(r"Retrying Function (\w+)", line)
            if fn: res.append(f":Retry {fn.group(1)};")
        else:
            res.append(f"note right: {line}")
    res.append("stop")
    res.append("@enduml")
    return "\n".join(res)

# --------------------------
# 🔄 Log Parser - Component
# --------------------------
def _build_comp(logs):
    """
    Parses logs to generate PlantUML component diagram.
    """
    res = ["@startuml"]
    blocks = set()
    for ln in logs:
        m = re.search(r"Function (\w+)", ln)
        if m:
            blocks.add(m.group(1))
    for b in blocks:
        res.append(f"component {b}")
    for ln in logs:
        c1 = re.search(r"Function (\w+) is called", ln)
        c2 = re.search(r"Function (\w+) is completed", ln)
        if c1 and c2:
            res.append(f"{c1.group(1)} --> {c2.group(1)}")
    res.append("@enduml")
    return "\n".join(res)

# --------------------------
# 🚀 Streamlit UI App
# --------------------------
st.set_page_config(page_title="Diagram Builder", layout="centered")
st.title("📊 Log Diagram Generator")
st.write("Upload or paste your logs to generate a sequence, activity, or component diagram.")

# Input block
uploaded = st.file_uploader("Upload log", type=["txt", "log"])
txt = st.text_area("Or paste logs here", height=200)

mode = st.radio("Choose diagram:", ("Sequence", "Activity", "Component"))
go = st.button("🎨 Build Diagram")

if (uploaded or txt) and go:
    lines = uploaded.read().decode("utf-8").splitlines() if uploaded else txt.splitlines()
    st.session_state['lines'] = lines
    st.session_state['mode'] = mode

    if mode == "Sequence":
        puml = _build_seq(lines)
    elif mode == "Activity":
        puml = _build_act(lines)
    elif mode == "Component":
        puml = _build_comp(lines)
    else:
        st.error("Invalid diagram mode.")
        puml = ""

    if puml:
        st.image(_generate_url(puml), caption=f"{mode} Diagram", use_container_width=True)

# --------------------------
# 🧰 Optional Filters
# --------------------------
if 'lines' in st.session_state and 'mode' in st.session_state:
    lines = st.session_state['lines']
    mode = st.session_state['mode']
    f_set, a_set = set(), set()

    for line in lines:
        fn = re.search(r"Function (\w+)", line)
        if fn: f_set.add(fn.group(1))
        if "is called" in line: a_set.add("is called")
        if "is completed" in line: a_set.add("is completed")
        if "caused error" in line: a_set.add("caused error")
        if "is skipped" in line: a_set.add("is skipped")
        if "Retrying" in line: a_set.add("Retrying")
        if "Timeout" in line: a_set.add("Timeout")

    with st.expander("🔍 Filter", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            sf = st.multiselect("Functions", sorted(f_set), default=list(f_set))
        with c2:
            sa = st.multiselect("Actions", sorted(a_set), default=list(a_set))
        trigger = st.button("⚙️ Filtered Diagram")

    if trigger:
        filtered = []
        for l in lines:
            fn = re.search(r"Function (\w+)", l)
            fname = fn.group(1) if fn else None

            action = None
            if "is called" in l: action = "is called"
            elif "is completed" in l: action = "is completed"
            elif "caused error" in l: action = "caused error"
            elif "is skipped" in l: action = "is skipped"
            elif "Retrying" in l: action = "Retrying"
            elif "Timeout" in l: action = "Timeout"

            if (not sf or (fname and fname in sf)) and (not sa or (action and action in sa)):
                filtered.append(l)

        if mode == "Sequence":
            fpuml = _build_seq(filtered)
        elif mode == "Activity":
            fpuml = _build_act(filtered)
        elif mode == "Component":
            fpuml = _build_comp(filtered)
        else:
            fpuml = ""

        if fpuml:
            st.image(_generate_url(fpuml), caption=f"Filtered {mode}", use_container_width=True)
