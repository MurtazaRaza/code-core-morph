//! The settings.
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

static FOLD: AtomicBool = AtomicBool::new(true);
static MIN: AtomicUsize = AtomicUsize::new(1);

pub fn set_fold_case(fold: bool) {
    FOLD.store(fold, Ordering::Relaxed);
}

pub fn fold_case() -> bool {
    FOLD.load(Ordering::Relaxed)
}

pub fn set_min_length(min: usize) {
    MIN.store(min, Ordering::Relaxed);
}

pub fn min_length() -> usize {
    MIN.load(Ordering::Relaxed)
}
