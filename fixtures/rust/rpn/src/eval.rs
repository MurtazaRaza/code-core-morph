//! Evaluates reverse Polish notation.

use std::fmt;

use crate::lexer::{tokenize, LexError, Op, Token};
use crate::stack::Stack;

/// Why an expression has no value.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EvalError {
    Lex(LexError),
    /// An operator with fewer than two values before it.
    Underflow,
    DivideByZero,
    /// Values left over: the expression needs more operators.
    Leftover(usize),
    Empty,
}

impl fmt::Display for EvalError {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        match self {
            EvalError::Lex(e) => write!(f, "{}", e),
            EvalError::Underflow => write!(f, "not enough values"),
            EvalError::DivideByZero => write!(f, "division by zero"),
            EvalError::Leftover(n) => write!(f, "{} values left over", n),
            EvalError::Empty => write!(f, "nothing to evaluate"),
        }
    }
}

fn apply(op: Op, a: f64, b: f64) -> Result<f64, EvalError> {
    match op {
        Op::Add => Ok(a + b),
        Op::Sub => Ok(a - b),
        Op::Mul => Ok(a * b),
        Op::Div if b == 0.0 => Err(EvalError::DivideByZero),
        Op::Div => Ok(a / b),
    }
}

/// The value of `tokens`, in reverse Polish notation.
pub fn eval(tokens: &[Token]) -> Result<f64, EvalError> {
    let mut stack = Stack::new();
    for token in tokens {
        match *token {
            Token::Num(n) => stack.push(n),
            Token::Op(op) => {
                let b = stack.pop().ok_or(EvalError::Underflow)?;
                let a = stack.pop().ok_or(EvalError::Underflow)?;
                stack.push(apply(op, a, b)?);
            }
        }
    }
    match stack.len() {
        0 => Err(EvalError::Empty),
        1 => Ok(stack.pop().unwrap()),
        n => Err(EvalError::Leftover(n - 1)),
    }
}

/// The value of an expression written out, such as `"3 4 + 2 *"`.
pub fn eval_str(input: &str) -> Result<f64, EvalError> {
    let tokens = tokenize(input).map_err(EvalError::Lex)?;
    eval(&tokens)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn evaluates_in_order() {
        assert_eq!(eval_str("3 4 + 2 *"), Ok(14.0));
        assert_eq!(eval_str("10 4 -"), Ok(6.0));
        assert_eq!(eval_str("1 -2 /"), Ok(-0.5));
    }

    #[test]
    fn reports_what_went_wrong() {
        assert_eq!(eval_str("1 +"), Err(EvalError::Underflow));
        assert_eq!(eval_str("1 0 /"), Err(EvalError::DivideByZero));
        assert_eq!(eval_str("1 2 3 +"), Err(EvalError::Leftover(1)));
        assert_eq!(eval_str("   "), Err(EvalError::Empty));
        assert_eq!(
            eval_str("2 $"),
            Err(EvalError::Lex(LexError::BadChar { ch: '$', at: 2 }))
        );
        assert_eq!(EvalError::Leftover(2).to_string(), "2 values left over");
    }
}
