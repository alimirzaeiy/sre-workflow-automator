import re
from typing import List, Tuple, Optional, Any, Dict

# Token types
TOKEN_WORD = "WORD"
TOKEN_LBRACE = "LBRACE"
TOKEN_RBRACE = "RBRACE"
TOKEN_SEMICOLON = "SEMICOLON"
TOKEN_COMMENT = "COMMENT"

class Token:
    def __init__(self, token_type: str, value: str, start: int, end: int):
        self.type = token_type
        self.value = value
        self.start = start
        self.end = end

    def __repr__(self):
        return f"Token({self.type}, {repr(self.value)}, {self.start}, {self.end})"


def tokenize_nginx(content: str) -> List[Token]:
    """
    Tokenizes Nginx configuration respecting:
    - Single & double quotes (and escaping within quotes)
    - Comments (# ...)
    - Semicolons (;)
    - Braces ({ and })
    - Whitespace separation
    """
    tokens = []
    i = 0
    n = len(content)

    while i < n:
        c = content[i]

        # Whitespace
        if c in " \t\r\n":
            i += 1
            continue

        # Comment
        if c == "#":
            start = i
            while i < n and content[i] not in "\r\n":
                i += 1
            tokens.append(Token(TOKEN_COMMENT, content[start:i], start, i))
            continue

        # Structural characters
        if c == "{":
            tokens.append(Token(TOKEN_LBRACE, "{", i, i + 1))
            i += 1
            continue
        elif c == "}":
            tokens.append(Token(TOKEN_RBRACE, "}", i, i + 1))
            i += 1
            continue
        elif c == ";":
            tokens.append(Token(TOKEN_SEMICOLON, ";", i, i + 1))
            i += 1
            continue

        # Quoted string or word (or mixed, e.g. "prefix"$var'suffix')
        start = i
        word_buf = []
        while i < n and content[i] not in " \t\r\n{};#":
            char = content[i]
            if char in ("'", '"'):
                quote = char
                word_buf.append(char)
                i += 1
                while i < n:
                    if content[i] == "\\":
                        word_buf.append(content[i])
                        if i + 1 < n:
                            word_buf.append(content[i + 1])
                            i += 2
                        else:
                            i += 1
                        continue
                    elif content[i] == quote:
                        word_buf.append(content[i])
                        i += 1
                        break
                    else:
                        word_buf.append(content[i])
                        i += 1
            else:
                word_buf.append(char)
                i += 1

        val = "".join(word_buf)
        tokens.append(Token(TOKEN_WORD, val, start, i))

    return tokens


class BlockNode:
    def __init__(self, name: str, args: List[str], start_idx: int, end_idx: int, body_start: int, body_end: int):
        self.name = name
        self.args = args
        self.start_idx = start_idx    # Start index of directive in original string
        self.end_idx = end_idx        # End index after closing '}'
        self.body_start = body_start  # Index right after opening '{'
        self.body_end = body_end      # Index right before closing '}'
        self.directives: List[Tuple[str, List[str]]] = []
        self.sub_blocks: List['BlockNode'] = []


def parse_nginx_blocks(tokens: List[Token], content: str) -> List[BlockNode]:
    """
    Parses top-level blocks and nested blocks from tokens.
    Filters out comments for grammar parsing, but preserves original string offsets.
    """
    valid_tokens = [t for t in tokens if t.type != TOKEN_COMMENT]
    
    idx = 0
    total = len(valid_tokens)

    def parse_block_list() -> List[BlockNode]:
        nonlocal idx
        block_list = []
        current_words = []
        stmt_start = -1

        while idx < total:
            tok = valid_tokens[idx]
            if tok.type == TOKEN_WORD:
                if stmt_start == -1:
                    stmt_start = tok.start
                current_words.append(tok.value)
                idx += 1
            elif tok.type == TOKEN_SEMICOLON:
                current_words = []
                stmt_start = -1
                idx += 1
            elif tok.type == TOKEN_LBRACE:
                if not current_words:
                    b_name = ""
                    b_args = []
                else:
                    b_name = current_words[0]
                    b_args = current_words[1:]
                b_start = stmt_start if stmt_start != -1 else tok.start
                body_start = tok.end
                idx += 1
                
                block_node = BlockNode(b_name, b_args, b_start, -1, body_start, -1)
                
                child_words = []
                child_stmt_start = -1
                while idx < total:
                    child_tok = valid_tokens[idx]
                    if child_tok.type == TOKEN_RBRACE:
                        block_node.body_end = child_tok.start
                        block_node.end_idx = child_tok.end
                        idx += 1
                        break
                    elif child_tok.type == TOKEN_WORD:
                        if child_stmt_start == -1:
                            child_stmt_start = child_tok.start
                        child_words.append(child_tok.value)
                        idx += 1
                    elif child_tok.type == TOKEN_SEMICOLON:
                        if child_words:
                            block_node.directives.append((child_words[0], child_words[1:]))
                        child_words = []
                        child_stmt_start = -1
                        idx += 1
                    elif child_tok.type == TOKEN_LBRACE:
                        sub_name = child_words[0] if child_words else ""
                        sub_args = child_words[1:] if child_words else []
                        sub_start = child_stmt_start if child_stmt_start != -1 else child_tok.start
                        child_words = []
                        child_stmt_start = -1
                        
                        sub_node = BlockNode(sub_name, sub_args, sub_start, -1, child_tok.end, -1)
                        brace_depth = 1
                        idx += 1
                        while idx < total and brace_depth > 0:
                            sub_tok = valid_tokens[idx]
                            if sub_tok.type == TOKEN_LBRACE:
                                brace_depth += 1
                            elif sub_tok.type == TOKEN_RBRACE:
                                brace_depth -= 1
                                if brace_depth == 0:
                                    sub_node.body_end = sub_tok.start
                                    sub_node.end_idx = sub_tok.end
                                    idx += 1
                                    break
                            idx += 1
                        block_node.sub_blocks.append(sub_node)
                
                block_list.append(block_node)
                current_words = []
                stmt_start = -1
            elif tok.type == TOKEN_RBRACE:
                break

        return block_list

    return parse_block_list()


def clean_quote(val: str) -> str:
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    return val


def find_server_blocks(nodes: List[BlockNode]) -> List[BlockNode]:
    servers = []
    for node in nodes:
        if node.name == "server":
            servers.append(node)
        for sub in node.sub_blocks:
            if sub.name == "server":
                servers.append(sub)
            else:
                servers.extend(find_server_blocks([sub]))
    return servers


def matches_server_name(server_block: BlockNode, domain: str) -> bool:
    domain_clean = domain.lower().strip()
    for d_name, d_args in server_block.directives:
        if d_name == "server_name":
            for arg in d_args:
                val = clean_quote(arg).lower().strip()
                if val == domain_clean or (val.startswith("*.") and domain_clean.endswith(val[1:])):
                    return True
    return False


def is_ssl_server(server_block: BlockNode) -> bool:
    for d_name, d_args in server_block.directives:
        if d_name == "ssl" and any("on" in a.lower() for a in d_args):
            return True
        if d_name.startswith("ssl_certificate"):
            return True
        if d_name == "listen":
            for arg in d_args:
                clean_arg = clean_quote(arg)
                parts = clean_arg.split(":")
                port_candidate = parts[-1]
                if re.match(r'^443(\b|$)', port_candidate):
                    return True
                if "ssl" in [clean_quote(a).lower() for a in d_args]:
                    return True
    return False


def modify_or_insert_location(
    content: str,
    domain: str,
    location_path: str,
    new_location_block: str
) -> Tuple[bool, str, str]:
    tokens = tokenize_nginx(content)
    top_nodes = parse_nginx_blocks(tokens, content)
    server_blocks = find_server_blocks(top_nodes)

    if not server_blocks:
        return False, content, "No server blocks found in configuration file."

    matching_servers = [s for s in server_blocks if matches_server_name(s, domain)]
    if not matching_servers:
        if len(server_blocks) == 1:
            matching_servers = server_blocks
        else:
            return False, content, f"No server block with server_name matching '{domain}' found."

    chosen_server = None
    for s in matching_servers:
        if is_ssl_server(s):
            chosen_server = s
            break
    if not chosen_server:
        chosen_server = matching_servers[0]

    clean_path = "/" + location_path.strip("/") if location_path.strip("/") else "/"

    for sub in chosen_server.sub_blocks:
        if sub.name == "location":
            sub_paths = [clean_quote(a) for a in sub.args]
            if clean_path in sub_paths or ("/" + clean_path.strip("/") in [p.rstrip("/") for p in sub_paths]):
                # Safe slice replacement without regex backreference issues
                # sub.start_idx to sub.end_idx includes whole block
                new_content = content[:sub.start_idx] + new_location_block.strip() + content[sub.end_idx:]
                return True, new_content, f"Existing location '{clean_path}' replaced cleanly."

    insert_pos = -1
    for sub in chosen_server.sub_blocks:
        if sub.name == "location":
            insert_pos = sub.start_idx
            break
            
    if insert_pos != -1:
        formatted_block = "\n    " + new_location_block.strip() + "\n\n    "
    else:
        insert_pos = chosen_server.body_end
        formatted_block = "\n    " + new_location_block.strip() + "\n"

    new_content = content[:insert_pos] + formatted_block + content[insert_pos:]
    return True, new_content, f"Location '{clean_path}' inserted into server block for '{domain}'."
