//! Word counts.
use crate::conf;

pub struct Table {
    words: Vec<(Vec<u8>, u64)>,
}

pub struct Word<'a> {
    text: &'a [u8],
    count: u64,
}

impl Table {
    pub fn new() -> Self {
        Table { words: Vec::new() }
    }

    pub fn count_text(&mut self, text: &[u8]) {
        for word in text.split(|c| !c.is_ascii_alphanumeric()) {
            if word.len() < conf::min_length() {
                continue;
            }
            let word = if conf::fold_case() { word.to_ascii_lowercase() } else { word.to_vec() };
            match self.words.iter_mut().find(|(w, _)| *w == word) {
                Some((_, n)) => *n += 1,
                None => self.words.push((word, 1)),
            }
        }
    }

    pub fn top(&self, n: usize) -> Vec<Word<'_>> {
        let mut all: Vec<Word<'_>> =
            self.words.iter().map(|(t, c)| Word { text: t, count: *c }).collect();
        all.sort_by(|a, b| b.count.cmp(&a.count).then(a.text.cmp(b.text)));
        all.truncate(n);
        all
    }
}

impl Default for Table {
    fn default() -> Self {
        Self::new()
    }
}

impl Word<'_> {
    pub fn text(&self) -> &[u8] {
        self.text
    }

    pub fn count(&self) -> u64 {
        self.count
    }
}
