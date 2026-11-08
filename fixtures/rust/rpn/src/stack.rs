//! A last-in, first-out stack.

/// A stack of values, the newest on top.
#[derive(Debug, Default)]
pub struct Stack<T> {
    items: Vec<T>,
}

impl<T: Copy> Stack<T> {
    pub fn new() -> Self {
        Stack { items: Vec::new() }
    }

    pub fn push(&mut self, value: T) {
        self.items.push(value);
    }

    /// The top value, removed; None when the stack is empty.
    pub fn pop(&mut self) -> Option<T> {
        self.items.pop()
    }

    /// The top value, left in place.
    pub fn peek(&self) -> Option<T> {
        self.items.last().copied()
    }

    pub fn len(&self) -> usize {
        self.items.len()
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn last_in_first_out() {
        let mut s = Stack::new();
        s.push(1.0);
        s.push(2.5);
        assert_eq!(s.len(), 2);
        assert_eq!(s.peek(), Some(2.5));
        assert_eq!(s.pop(), Some(2.5));
        assert_eq!(s.pop(), Some(1.0));
        assert_eq!(s.pop(), None);
        assert!(s.is_empty());
    }
}
