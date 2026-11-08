//! Splits an expression into numbers and operators.

use std::fmt;

/// An arithmetic operator.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Op {
    Add,
    Sub,
    Mul,
    Div,
}

/// One token of an expression.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Token {
    Num(f64),
    Op(Op),
}

/// Why an expression couldn't be split.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LexError {
    /// A character that is neither part of a number nor an operator, at a byte offset.
    BadChar { ch: char, at: usize },
    /// A number that doesn't parse, such as `1.2.3`.
    BadNumber { at: usize },
}

impl fmt::Display for LexError {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        match *self {
            LexError::BadChar { ch, at } => write!(f, "unexpected '{}' at {}", ch, at),
            LexError::BadNumber { at } => write!(f, "bad number at {}", at),
        }
    }
}

/// The tokens of `input`, separated by whitespace.
pub fn tokenize(input: &str) -> Result<Vec<Token>, LexError> {
    let mut tokens = Vec::new();
    let bytes = input.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        let c = bytes[i] as char;
        if c.is_ascii_whitespace() {
            i += 1;
            continue;
        }
        let op = match c {
            '+' => Some(Op::Add),
            '-' => Some(Op::Sub),
            '*' => Some(Op::Mul),
            '/' => Some(Op::Div),
            _ => None,
        };
        let next_is_digit = bytes.get(i + 1).map_or(false, |b| b.is_ascii_digit());
        if let Some(op) = op {
            if !(op == Op::Sub && next_is_digit) {
                tokens.push(Token::Op(op));
                i += 1;
                continue;
            }
        }
        if c.is_ascii_digit() || c == '.' || c == '-' {
            let start = i;
            i += 1;
            while i < bytes.len() && (bytes[i].is_ascii_digit() || bytes[i] == b'.') {
                i += 1;
            }
            match input[start..i].parse::<f64>() {
                Ok(n) => tokens.push(Token::Num(n)),
                Err(_) => return Err(LexError::BadNumber { at: start }),
            }
            continue;
        }
        let ch = input[i..].chars().next().unwrap_or('?');
        return Err(LexError::BadChar { ch, at: i });
    }
    Ok(tokens)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numbers_and_operators() {
        let tokens = tokenize("3 4.5 + -2 *").unwrap();
        assert_eq!(
            tokens,
            vec![
                Token::Num(3.0),
                Token::Num(4.5),
                Token::Op(Op::Add),
                Token::Num(-2.0),
                Token::Op(Op::Mul),
            ]
        );
    }

    #[test]
    fn a_minus_alone_is_an_operator() {
        assert_eq!(tokenize("5 1 -").unwrap()[2], Token::Op(Op::Sub));
        assert_eq!(tokenize("").unwrap(), vec![]);
    }

    #[test]
    fn errors_say_where() {
        assert_eq!(tokenize("1 x"), Err(LexError::BadChar { ch: 'x', at: 2 }));
        assert_eq!(tokenize("1.2.3"), Err(LexError::BadNumber { at: 0 }));
        assert_eq!(LexError::BadChar { ch: 'x', at: 2 }.to_string(), "unexpected 'x' at 2");
    }
}
