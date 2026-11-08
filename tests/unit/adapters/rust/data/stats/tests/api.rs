use stats::stats::{mean, median, Histogram};
use stats::text::mean_word_length;

#[test]
fn the_public_api() {
    assert_eq!(mean(&[2.0, 4.0]), Some(3.0));
    assert_eq!(median(&mut [5.0]), Some(5.0));
    let mut h = Histogram::new(2, -1.0, 1.0).unwrap();
    h.add(-0.5).unwrap();
    assert_eq!(h.count(0), Some(1));
    assert_eq!(mean_word_length("one three"), Some(4.0));
}
