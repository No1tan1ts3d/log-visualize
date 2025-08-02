import streamlit as st
import re
import zlib
import base64
from functools import wraps
from typing import Dict, List, Optional, Tuple, Set, Any
import hashlib

# === Configuration Constants ===
_CFG = {
    'srv_url': "http://www.plantuml.com/plantuml/png/",
    'enc_alphabet': '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_',
    'header_strip': [2, -4],
    'chunk_size': 3,
    'max_note_len': 50,
    'preview_lines': 10,
    'default_funcs': 10
}

# === Utility Decorators ===
def _memoize(func):
    cache = {}
    @wraps(func)
    def wrapper(*args, **kwargs):
        key = str(args) + str(sorted(kwargs.items()))
        if key not in cache:
            cache[key] = func(*args, **kwargs)
        return cache[key]
    return wrapper

def _error_handler(default_return=None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception:
                return default_return
        return wrapper
    return decorator

# === Core Engine Classes ===
class _PlantUMLEncoder:
    """Handles PlantUML URL encoding with custom compression"""
    
    def __init__(self, alphabet: str = None):
        self._alphabet = alphabet or _CFG['enc_alphabet']
        self._base_url = _CFG['srv_url']
    
    @_error_handler("")
    def _compress_data(self, text: str) -> bytes:
        data = text.encode('utf-8')
        compressed = zlib.compress(data)
        return compressed[_CFG['header_strip'][0]:_CFG['header_strip'][1]]
    
    @_error_handler("")
    def _encode_b64_custom(self, data: bytes) -> str:
        result, i = '', 0
        while i < len(data):
            triplet = [
                data[i],
                data[i + 1] if i + 1 < len(data) else 0,
                data[i + 2] if i + 2 < len(data) else 0
            ]
            
            encoded_chars = [
                self._alphabet[triplet[0] >> 2],
                self._alphabet[((triplet[0] & 0x3) << 4) | (triplet[1] >> 4)],
                self._alphabet[((triplet[1] & 0xF) << 2) | (triplet[2] >> 6)],
                self._alphabet[triplet[2] & 0x3F]
            ]
            
            result += ''.join(encoded_chars)
            i += _CFG['chunk_size']
        return result
    
    def generate_url(self, uml_content: str) -> str:
        compressed = self._compress_data(uml_content)
        encoded = self._encode_b64_custom(compressed)
        return f"{self._base_url}{encoded}"

class _LogPattern:
    """Pattern matching for different log formats"""
    
    PATTERNS = {
        'qdma_main': r'\[[\d.]+\]\s+(\w+):(\w+):\s+----- QDMA (entering|exiting) the (\w+) function at.*?\[Thread ID: (\d+)\]',
        'qdma_simple': r'\[[\d.]+\]\s+(\w+):(\w+):\s+(.+)$',
        'command_exec': r'\[[\d.]+\]\s+Command:\s+(.+)$',
        'legacy_func': r"\bFunction (\w+)\b.*?\b(entering|command|info|exiting|called|completed|error|retry|skipped)\b"
    }
    
    @classmethod
    @_memoize
    def match_pattern(cls, line: str, pattern_key: str) -> Optional[re.Match]:
        return re.search(cls.PATTERNS.get(pattern_key, ''), line, re.IGNORECASE)

class _LogEntry:
    """Represents a parsed log entry"""
    
    __slots__ = ['module', 'caller_func', 'function', 'action', 'thread_id', 'message', 'full_line', '_hash']
    
    def __init__(self, **kwargs):
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))
        self._hash = None
    
    def __hash__(self):
        if self._hash is None:
            data = f"{self.module}{self.function}{self.action}"
            self._hash = int(hashlib.md5(data.encode()).hexdigest()[:8], 16)
        return self._hash
    
    def __eq__(self, other):
        return isinstance(other, _LogEntry) and hash(self) == hash(other)

class _LogParser:
    """Main log parsing engine"""
    
    @staticmethod
    @_error_handler(None)
    def _parse_qdma_entry(line: str) -> Optional[_LogEntry]:
        # Try main QDMA pattern
        match = _LogPattern.match_pattern(line, 'qdma_main')
        if match:
            groups = match.groups()
            return _LogEntry(
                module=groups[0], caller_func=groups[1], function=groups[3],
                action=groups[2], thread_id=groups[4], full_line=line.strip()
            )
        
        # Try simple pattern
        match = _LogPattern.match_pattern(line, 'qdma_simple')
        if match:
            groups = match.groups()
            return _LogEntry(
                module=groups[0], caller_func=groups[1], function=groups[1],
                action='info', thread_id=None, message=groups[2], full_line=line.strip()
            )
        
        # Try command pattern
        match = _LogPattern.match_pattern(line, 'command_exec')
        if match:
            return _LogEntry(
                module='system', caller_func='command', function='command',
                action='command', thread_id=None, message=match.group(1), full_line=line.strip()
            )
        
        return None
    
    @classmethod
    def detect_format(cls, lines: List[str]) -> str:
        qdma_score = legacy_score = 0
        
        for line in lines[:_CFG['preview_lines']]:
            if any(indicator in line for indicator in ['qdma_pf:', 'QDMA entering', 'QDMA exiting']):
                qdma_score += 1
            elif all(indicator in line for indicator in ['Function', ('is called', 'is completed')]):
                legacy_score += 1
        
        return "qdma" if qdma_score > legacy_score else "legacy"
    
    @classmethod
    def parse_lines(cls, lines: List[str], format_type: str = None) -> List[_LogEntry]:
        if format_type is None:
            format_type = cls.detect_format(lines)
        
        entries = []
        for line in lines:
            if format_type == "qdma":
                entry = cls._parse_qdma_entry(line)
                if entry:
                    entries.append(entry)
        
        return entries

class _DiagramGenerator:
    """Generates PlantUML diagrams from parsed log entries"""
    
    def __init__(self):
        self._encoder = _PlantUMLEncoder()
    
    def _build_sequence_diagram(self, entries: List[_LogEntry]) -> str:
        lines = ["@startuml", "title QDMA Driver Function Call Sequence", "participant User"]
        participants = {"User"}
        call_stack = []
        
        for entry in entries:
            func_name = entry.function
            
            if func_name not in participants:
                lines.append(f"participant {func_name}")
                participants.add(func_name)
            
            self._process_sequence_action(entry, lines, call_stack)
        
        lines.append("@enduml")
        return "\n".join(lines)
    
    def _process_sequence_action(self, entry: _LogEntry, lines: List[str], call_stack: List[str]):
        action_handlers = {
            'entering': self._handle_entering,
            'exiting': self._handle_exiting,
            'command': self._handle_command,
            'info': self._handle_info
        }
        
        handler = action_handlers.get(entry.action)
        if handler:
            handler(entry, lines, call_stack)
    
    def _handle_entering(self, entry: _LogEntry, lines: List[str], call_stack: List[str]):
        caller = call_stack[-1] if call_stack else "User"
        lines.append(f"{caller}->{entry.function}: {entry.action}")
        call_stack.append(entry.function)
    
    def _handle_exiting(self, entry: _LogEntry, lines: List[str], call_stack: List[str]):
        if call_stack and call_stack[-1] == entry.function:
            call_stack.pop()
            target = call_stack[-1] if call_stack else "User"
            lines.append(f"{entry.function}-->{target}: {entry.action}")
    
    def _handle_command(self, entry: _LogEntry, lines: List[str], call_stack: List[str]):
        lines.append(f"note over User: {getattr(entry, 'message', '')}")
    
    def _handle_info(self, entry: _LogEntry, lines: List[str], call_stack: List[str]):
        if hasattr(entry, 'message') and entry.message:
            truncated = entry.message[:_CFG['max_note_len']] + "..."
            lines.append(f"note right of {entry.function}: {truncated}")
    
    def _build_activity_diagram(self, entries: List[_LogEntry]) -> str:
        lines = ["@startuml", "title QDMA Driver Activity Flow", "start"]
        
        for entry in entries:
            if entry.action == 'entering':
                lines.append(f":Enter {entry.function};")
            elif entry.action == 'exiting':
                lines.append(f":Exit {entry.function};")
            elif entry.action == 'command':
                cmd_text = getattr(entry, 'message', '')[:30] + "..."
                lines.append(f":Execute Command\\n{cmd_text};")
            elif entry.action == 'info' and hasattr(entry, 'message'):
                note_text = entry.message[:40] + "..."
                lines.append(f"note right: {note_text}")
        
        lines.extend(["stop", "@enduml"])
        return "\n".join(lines)
    
    def _build_component_diagram(self, entries: List[_LogEntry]) -> str:
        lines = ["@startuml", "title QDMA Driver Component Interaction"]
        
        # Group by modules
        module_funcs = {}
        for entry in entries:
            if entry.module not in module_funcs:
                module_funcs[entry.module] = set()
            module_funcs[entry.module].add(entry.function)
        
        # Generate components
        for module, funcs in sorted(module_funcs.items()):
            lines.append(f"package {module} {{")
            for func in sorted(funcs):
                lines.append(f"  component {func}")
            lines.append("}")
        
        # Generate interactions
        prev_func = None
        for entry in entries:
            if entry.action == 'entering':
                if prev_func and prev_func != entry.function:
                    lines.append(f"{prev_func} --> {entry.function}")
                prev_func = entry.function
        
        lines.append("@enduml")
        return "\n".join(lines)
    
    def generate_diagram(self, entries: List[_LogEntry], diagram_type: str) -> Tuple[str, str]:
        """Returns (puml_content, image_url)"""
        generators = {
            "Sequence Diagram": self._build_sequence_diagram,
            "Activity Diagram": self._build_activity_diagram,
            "Component Diagram": self._build_component_diagram
        }
        
        generator = generators.get(diagram_type)
        if not generator:
            return "", ""
        
        puml_content = generator(entries)
        image_url = self._encoder.generate_url(puml_content)
        
        return puml_content, image_url

class _FilterEngine:
    """Handles filtering of log entries"""
    
    @staticmethod
    def extract_metadata(entries: List[_LogEntry]) -> Dict[str, Set[str]]:
        metadata = {
            'functions': set(),
            'modules': set(),
            'actions': set(),
            'threads': set()
        }
        
        for entry in entries:
            metadata['functions'].add(entry.function)
            metadata['modules'].add(entry.module)
            metadata['actions'].add(entry.action)
            if entry.thread_id:
                metadata['threads'].add(entry.thread_id)
        
        return metadata
    
    @staticmethod
    def apply_filters(entries: List[_LogEntry], filters: Dict[str, List[str]]) -> List[_LogEntry]:
        filtered = []
        
        for entry in entries:
            if (not filters.get('functions') or entry.function in filters['functions']) and \
               (not filters.get('modules') or entry.module in filters['modules']) and \
               (not filters.get('actions') or entry.action in filters['actions']) and \
               (not filters.get('threads') or not entry.thread_id or entry.thread_id in filters['threads']):
                filtered.append(entry)
        
        return filtered

# === Legacy Support (Minimal Implementation) ===
class _LegacyParser:
    """Backward compatibility for old log formats"""
    
    @staticmethod
    def parse_to_puml(lines: List[str]) -> str:
        puml_lines = ["@startuml", "participant Caller"]
        participants = {"Caller"}
        
        for line in lines:
            match = _LogPattern.match_pattern(line.strip(), 'legacy_func')
            if match:
                fn, action = match.groups()
                if fn not in participants:
                    puml_lines.append(f"participant {fn}")
                    participants.add(fn)
                
                if action.lower() in ["entering", "called", "command", "retry"]:
                    puml_lines.append(f"Caller -> {fn}: {action}")
                elif action.lower() in ["exiting", "completed"]:
                    puml_lines.append(f"{fn} --> Caller: {action}")
                else:
                    puml_lines.append(f"note right of {fn}: {action.upper()}")
        
        puml_lines.append("@enduml")
        return "\n".join(puml_lines)

# === Main Application Logic ===
def _initialize_session_state():
    """Initialize Streamlit session state"""
    if 'parser' not in st.session_state:
        st.session_state.parser = _LogParser()
    if 'diagram_gen' not in st.session_state:
        st.session_state.diagram_gen = _DiagramGenerator()
    if 'filter_engine' not in st.session_state:
        st.session_state.filter_engine = _FilterEngine()

def _render_main_interface():
    """Render the main UI components"""
    st.set_page_config(page_title="Enhanced Log Diagram Visualizer", layout="wide")
    st.title("📊 Enhanced Log File to Diagram Visualizer")
    st.write("Upload or paste your log file below to generate a visual diagram. Supports QDMA driver logs.")
    
    # Input components
    uploaded_file = st.file_uploader("Upload log file", type=["txt", "log"])
    log_text = st.text_area("Or paste log content here", height=200)
    diagram_type = st.radio("Select diagram type:", ("Sequence Diagram", "Activity Diagram", "Component Diagram"))
    
    return uploaded_file, log_text, diagram_type

def _process_log_input(uploaded_file, log_text) -> Optional[List[str]]:
    """Process log input from file or text"""
    if uploaded_file:
        return uploaded_file.read().decode("utf-8").splitlines()
    elif log_text:
        return log_text.splitlines()
    return None

def _render_filtering_interface(entries: List[_LogEntry], metadata: Dict[str, Set[str]]):
    """Render the filtering interface"""
    with st.expander("🔍 Advanced Filter Options", expanded=False):
        col1, col2, col3 = st.columns(3)
        
        with col1:
            selected_functions = st.multiselect(
                "Filter by Functions",
                sorted(metadata['functions']),
                default=list(sorted(metadata['functions']))[:_CFG['default_funcs']] 
                        if len(metadata['functions']) > _CFG['default_funcs'] 
                        else list(metadata['functions']),
                help="Select specific functions to include in the diagram"
            )
        
        with col2:
            selected_modules = st.multiselect(
                "Filter by Modules",
                sorted(metadata['modules']),
                default=list(metadata['modules']),
                help="Select QDMA modules to include"
            )
        
        with col3:
            selected_actions = st.multiselect(
                "Filter by Actions",
                sorted(metadata['actions']),
                default=list(metadata['actions']),
                help="Select action types to include"
            )
        
        selected_threads = []
        if metadata['threads']:
            selected_threads = st.multiselect(
                "Filter by Thread ID",
                sorted(metadata['threads']),
                default=list(metadata['threads']),
                help="Select specific thread IDs"
            )
        
        return {
            'functions': selected_functions,
            'modules': selected_modules,
            'actions': selected_actions,
            'threads': selected_threads
        }, st.button("🎯 Generate Filtered Diagram")

def main():
    """Main application entry point"""
    _initialize_session_state()
    
    uploaded_file, log_text, diagram_type = _render_main_interface()
    submit = st.button("🔍 Generate Diagram")
    
    if (uploaded_file or log_text) and submit:
        log_lines = _process_log_input(uploaded_file, log_text)
        if not log_lines:
            st.error("No log content found")
            return
        
        # Parse logs
        entries = st.session_state.parser.parse_lines(log_lines)
        if not entries:
            # Fallback to legacy parser
            legacy_parser = _LegacyParser()
            puml_content = legacy_parser.parse_to_puml(log_lines)
            encoder = _PlantUMLEncoder()
            image_url = encoder.generate_url(puml_content)
            st.image(image_url, caption=f"Generated {diagram_type}", use_container_width=True)
            return
        
        st.session_state.entries = entries
        st.session_state.diagram_type = diagram_type
        
        # Generate diagram
        puml_content, image_url = st.session_state.diagram_gen.generate_diagram(entries, diagram_type)
        
        if image_url:
            st.image(image_url, caption=f"Generated {diagram_type}", use_container_width=True)
    
    # Filtering interface
    if 'entries' in st.session_state:
        entries = st.session_state.entries
        metadata = st.session_state.filter_engine.extract_metadata(entries)
        
        filters, filter_submit = _render_filtering_interface(entries, metadata)
        
        if filter_submit:
            filtered_entries = st.session_state.filter_engine.apply_filters(entries, filters)
            puml_content, image_url = st.session_state.diagram_gen.generate_diagram(
                filtered_entries, st.session_state.diagram_type
            )
            
            if image_url:
                st.subheader("🎯 Filtered Diagram")
                st.image(image_url, caption=f"Filtered {st.session_state.diagram_type}", use_container_width=True)
    
    if not (uploaded_file or log_text):
        st.info("📂 Please upload a log file or paste log content, select diagram type, and click Generate Diagram.")

if __name__ == "__main__":
    main()
