//! Text helpers that use the statistics.

use crate::stats;

/// The mean length of the whitespace-separated words, or None without words.
pub fn mean_word_length(text: &str) -> Option<f64> {
    let lengths: Vec<f64> = text.split_whitespace().map(|w| w.chars().count() as f64).collect();
    stats::mean(&lengths)
}

#[cfg(test)]
mod tests {
    #[test]
    fn words() {
        assert_eq!(super::mean_word_length("ab abcd"), Some(3.0));
        assert_eq!(super::mean_word_length("  "), None);
    }
}
