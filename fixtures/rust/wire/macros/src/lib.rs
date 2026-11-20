//! wire_macros: the wire crate's own procedural macros. No dependencies: the input is read
//! from the raw token stream and the output written as source text.

use proc_macro::{Delimiter, Spacing, TokenStream, TokenTree};

/// `#[derive(Encode)]`: each field in order (a struct), or a tag byte and the variant's fields
/// (an enum). A field marked `#[wire(skip)]` isn't written.
#[proc_macro_derive(Encode, attributes(wire))]
pub fn derive_encode(input: TokenStream) -> TokenStream {
    let item = parse_item(input);
    let body = match &item.shape {
        Shape::Struct(fields) => {
            let mut encode = String::new();
            let mut len = String::from("0usize");
            for f in fields.iter().filter(|f| !f.skip) {
                encode += &format!("::wire::codec::Encode::encode(&self.{}, out);\n", f.name);
                len += &format!(" + ::wire::codec::Encode::encoded_len(&self.{})", f.name);
            }
            format!(
                "fn encode(&self, out: &mut ::std::vec::Vec<u8>) {{\n{}}}\n\
                 fn encoded_len(&self) -> usize {{ {} }}\n",
                encode, len
            )
        }
        Shape::Enum(variants) => {
            let mut encode = String::new();
            let mut len = String::new();
            for (tag, v) in variants.iter().enumerate() {
                let (pattern, names) = v.pattern(&item.name);
                let writes: String = names
                    .iter()
                    .map(|n| format!("::wire::codec::Encode::encode({}, out);\n", n))
                    .collect();
                let sizes: String = names
                    .iter()
                    .map(|n| format!(" + ::wire::codec::Encode::encoded_len({})", n))
                    .collect();
                encode += &format!("{} => {{ out.push({}u8);\n{}}}\n", pattern, tag, writes);
                len += &format!("{} => 1usize{},\n", pattern, sizes);
            }
            format!(
                "fn encode(&self, out: &mut ::std::vec::Vec<u8>) {{ match self {{\n{}}} }}\n\
                 fn encoded_len(&self) -> usize {{ match self {{\n{}}} }}\n",
                encode, len
            )
        }
    };
    format!("impl ::wire::codec::Encode for {} {{\n{}}}\n", item.name, body)
        .parse()
        .unwrap()
}

/// `#[derive(Decode)]`: the reverse of `Encode`; a skipped field takes its `Default`, an
/// unknown tag is `DecodeError::BadTag`.
#[proc_macro_derive(Decode, attributes(wire))]
pub fn derive_decode(input: TokenStream) -> TokenStream {
    let item = parse_item(input);
    let read = "::wire::codec::Decode::decode(input)?";
    let body = match &item.shape {
        Shape::Struct(fields) => {
            let mut inits = String::new();
            for f in fields {
                let value = if f.skip { "::core::default::Default::default()" } else { read };
                inits += &format!("{}: {},\n", f.name, value);
            }
            format!("::core::result::Result::Ok({} {{\n{}}})", item.name, inits)
        }
        Shape::Enum(variants) => {
            let mut arms = String::new();
            for (tag, v) in variants.iter().enumerate() {
                let value = match &v.fields {
                    Fields::Unit => format!("{}::{}", item.name, v.name),
                    Fields::Tuple(n) => {
                        let reads = vec![read; *n].join(", ");
                        format!("{}::{}({})", item.name, v.name, reads)
                    }
                    Fields::Named(names) => {
                        let inits: Vec<String> =
                            names.iter().map(|n| format!("{}: {}", n, read)).collect();
                        format!("{}::{} {{ {} }}", item.name, v.name, inits.join(", "))
                    }
                };
                arms += &format!("{}u8 => {},\n", tag, value);
            }
            format!(
                "let tag: u8 = {};\n::core::result::Result::Ok(match tag {{\n{}\
                 other => return ::core::result::Result::Err(\
                 ::wire::codec::DecodeError::BadTag(other)),\n}})",
                read, arms
            )
        }
    };
    format!(
        "impl ::wire::codec::Decode for {} {{\n\
         fn decode(input: &mut &[u8]) -> ::core::result::Result<Self, ::wire::codec::DecodeError> \
         {{\n{}\n}}\n}}\n",
        item.name, body
    )
    .parse()
    .unwrap()
}

/// `#[throws(E)]` on a function returning `T`: it returns `Result<T, E>`, its body's value
/// is the `Ok`, and `?` or `return Err(..)` inside it throws.
#[proc_macro_attribute]
pub fn throws(attr: TokenStream, item: TokenStream) -> TokenStream {
    let error = attr.to_string();
    let tokens: Vec<TokenTree> = item.into_iter().collect();
    let body_at = tokens
        .iter()
        .rposition(|t| matches!(t, TokenTree::Group(g) if g.delimiter() == Delimiter::Brace))
        .expect("#[throws] goes on a function with a body");
    let arrow = (0..body_at).find(|&i| {
        matches!(&tokens[i], TokenTree::Punct(p) if p.as_char() == '-' && p.spacing() == Spacing::Joint)
            && matches!(&tokens[i + 1], TokenTree::Punct(p) if p.as_char() == '>')
    });
    let where_at = (0..body_at)
        .find(|&i| matches!(&tokens[i], TokenTree::Ident(id) if id.to_string() == "where"))
        .unwrap_or(body_at);
    let (head, returns) = match arrow {
        Some(a) => (&tokens[..a], stream(&tokens[a + 2..where_at])),
        None => (&tokens[..where_at], "()".to_string()),
    };
    format!(
        "{} -> ::core::result::Result<{}, {}> {} {{ ::core::result::Result::Ok({}) }}",
        stream(head),
        returns,
        error,
        stream(&tokens[where_at..body_at]),
        tokens[body_at]
    )
    .parse()
    .unwrap()
}

/// `crc_table!(POLY)`: the 256 entries of a reflected CRC-16's lookup table, computed while
/// the crate compiles.
#[proc_macro]
pub fn crc_table(input: TokenStream) -> TokenStream {
    let text = input.to_string().replace('_', "");
    let poly = u16::from_str_radix(text.trim().trim_start_matches("0x"), 16)
        .expect("crc_table! takes a hexadecimal polynomial");
    let entries: Vec<String> = table(poly).iter().map(|e| format!("0x{:04X}u16", e)).collect();
    format!("[{}]", entries.join(", ")).parse().unwrap()
}

/// The table `crc_table!` writes out.
fn table(poly: u16) -> Vec<u16> {
    (0..256u16)
        .map(|i| {
            let mut crc = i;
            for _ in 0..8 {
                crc = if crc & 1 != 0 { (crc >> 1) ^ poly } else { crc >> 1 };
            }
            crc
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_arc_table() {
        let t = table(0xA001);
        assert_eq!(t.len(), 256);
        assert_eq!((t[0], t[1], t[255]), (0x0000, 0xC0C1, 0x4040));
    }
}

// ------------------------------------------------------------------ reading the item

struct Item {
    name: String,
    shape: Shape,
}

enum Shape {
    Struct(Vec<Field>),
    Enum(Vec<Variant>),
}

struct Field {
    name: String,
    skip: bool,
}

struct Variant {
    name: String,
    fields: Fields,
}

enum Fields {
    Unit,
    Tuple(usize),
    Named(Vec<String>),
}

impl Variant {
    /// The variant's match pattern, binding its fields, and the names it binds.
    fn pattern(&self, ty: &str) -> (String, Vec<String>) {
        match &self.fields {
            Fields::Unit => (format!("{}::{}", ty, self.name), vec![]),
            Fields::Tuple(n) => {
                let names: Vec<String> = (0..*n).map(|i| format!("f{}", i)).collect();
                (format!("{}::{}({})", ty, self.name, names.join(", ")), names)
            }
            Fields::Named(names) => (
                format!("{}::{} {{ {} }}", ty, self.name, names.join(", ")),
                names.clone(),
            ),
        }
    }
}

fn stream(tokens: &[TokenTree]) -> String {
    tokens.iter().cloned().collect::<TokenStream>().to_string()
}

fn parse_item(input: TokenStream) -> Item {
    let tokens: Vec<TokenTree> = input.into_iter().collect();
    let keyword = tokens
        .iter()
        .position(|t| matches!(t, TokenTree::Ident(id) if id.to_string() == "struct" || id.to_string() == "enum"))
        .expect("derive(Encode, Decode) takes a struct or an enum");
    let is_enum = tokens[keyword].to_string() == "enum";
    let name = tokens[keyword + 1].to_string();
    if matches!(&tokens[keyword + 2], TokenTree::Punct(p) if p.as_char() == '<') {
        panic!("derive(Encode, Decode) takes no generic types");
    }
    let body = tokens[keyword + 2..]
        .iter()
        .find_map(|t| match t {
            TokenTree::Group(g) if g.delimiter() == Delimiter::Brace => Some(g.stream()),
            _ => None,
        })
        .expect("derive(Encode, Decode) takes a struct with named fields, or an enum");
    let shape = if is_enum {
        Shape::Enum(split(body).into_iter().map(parse_variant).collect())
    } else {
        Shape::Struct(split(body).into_iter().map(parse_field).collect())
    };
    Item { name, shape }
}

/// Splits at the commas outside angle brackets.
fn split(stream: TokenStream) -> Vec<Vec<TokenTree>> {
    let mut parts = vec![vec![]];
    let mut depth = 0i32;
    for t in stream {
        match &t {
            TokenTree::Punct(p) if p.as_char() == '<' => depth += 1,
            TokenTree::Punct(p) if p.as_char() == '>' => depth -= 1,
            TokenTree::Punct(p) if p.as_char() == ',' && depth == 0 => {
                parts.push(vec![]);
                continue;
            }
            _ => {}
        }
        parts.last_mut().unwrap().push(t);
    }
    parts.retain(|p| !p.is_empty());
    parts
}

/// The tokens after the attributes, and whether one of them was `#[wire(skip)]`.
fn strip_attributes(tokens: Vec<TokenTree>) -> (Vec<TokenTree>, bool) {
    let mut rest = vec![];
    let mut skip = false;
    let mut iter = tokens.into_iter().peekable();
    while let Some(t) = iter.next() {
        if matches!(&t, TokenTree::Punct(p) if p.as_char() == '#') {
            if let Some(TokenTree::Group(g)) = iter.next() {
                let text = g.stream().to_string().replace(' ', "");
                skip |= text == "wire(skip)";
            }
            continue;
        }
        rest.push(t);
    }
    (rest, skip)
}

fn parse_field(tokens: Vec<TokenTree>) -> Field {
    let (rest, skip) = strip_attributes(tokens);
    let colon = rest
        .iter()
        .position(|t| matches!(t, TokenTree::Punct(p) if p.as_char() == ':'))
        .expect("a named field");
    Field { name: rest[colon - 1].to_string(), skip }
}

fn parse_variant(tokens: Vec<TokenTree>) -> Variant {
    let (rest, _) = strip_attributes(tokens);
    let name = rest[0].to_string();
    let fields = match rest.get(1) {
        Some(TokenTree::Group(g)) if g.delimiter() == Delimiter::Parenthesis => {
            Fields::Tuple(split(g.stream()).len())
        }
        Some(TokenTree::Group(g)) if g.delimiter() == Delimiter::Brace => Fields::Named(
            split(g.stream()).into_iter().map(|f| parse_field(f).name).collect(),
        ),
        _ => Fields::Unit,
    };
    Variant { name, fields }
}
