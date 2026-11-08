//! Descriptive statistics over slices, and a fixed-width histogram. Implemented in C
//! (c/src/stats.c, declared in c/include/stats/stats.h) behind this unchanged API: the façade
//! is the one place with `unsafe` code.
#![allow(unsafe_code)]

use std::fmt;
use std::ptr::NonNull;

/// bindgen's view of c/include/stats/stats.h (written before each build; don't edit).
#[allow(non_camel_case_types, non_upper_case_globals, dead_code, clippy::all)]
mod sys;

/// The arithmetic mean, or None for an empty slice.
pub fn mean(xs: &[f64]) -> Option<f64> {
    let mut out = 0.0;
    // SAFETY: `xs` is valid for `xs.len()` reads and `out` for one write.
    let status = unsafe { sys::stats_mean(xs.as_ptr(), xs.len(), &mut out) };
    (status == sys::stats_status_STATS_OK).then_some(out)
}

/// The median; sorts `xs` in place. None for an empty slice.
pub fn median(xs: &mut [f64]) -> Option<f64> {
    let mut out = 0.0;
    // SAFETY: `xs` is valid for `xs.len()` reads and writes, `out` for one write.
    let status = unsafe { sys::stats_median(xs.as_mut_ptr(), xs.len(), &mut out) };
    (status == sys::stats_status_STATS_OK).then_some(out)
}

/// Counts of values in `bins` equal-width bins over `[lo, hi)`.
pub struct Histogram {
    raw: NonNull<sys::stats_histogram>,
}

/// Why a value couldn't be added.
#[derive(Debug, PartialEq, Eq)]
pub enum AddError {
    OutOfRange,
}

impl Histogram {
    /// None when there are no bins or the range is empty.
    pub fn new(bins: usize, lo: f64, hi: f64) -> Option<Self> {
        // SAFETY: plain values in; the C returns NULL or a histogram this value now owns.
        NonNull::new(unsafe { sys::stats_histogram_new(bins, lo, hi) }).map(|raw| Self { raw })
    }

    pub fn add(&mut self, x: f64) -> Result<(), AddError> {
        // SAFETY: `raw` is a live histogram, borrowed mutably.
        match unsafe { sys::stats_histogram_add(self.raw.as_ptr(), x) } {
            sys::stats_status_STATS_OK => Ok(()),
            _ => Err(AddError::OutOfRange),
        }
    }

    pub fn count(&self, bin: usize) -> Option<u32> {
        let mut out = 0;
        // SAFETY: `raw` is a live histogram; `out` is valid for one write.
        unsafe { sys::stats_histogram_count(self.raw.as_ptr(), bin, &mut out) }.then_some(out)
    }

    pub fn bins(&self) -> usize {
        // SAFETY: `raw` is a live histogram.
        unsafe { sys::stats_histogram_bins(self.raw.as_ptr()) }
    }
}

impl Drop for Histogram {
    fn drop(&mut self) {
        // SAFETY: this value owns the histogram, and nothing uses it after the drop.
        unsafe { sys::stats_histogram_free(self.raw.as_ptr()) }
    }
}

impl fmt::Debug for Histogram {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let counts: Vec<u32> = (0..self.bins()).filter_map(|i| self.count(i)).collect();
        f.debug_struct("Histogram").field("counts", &counts).finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mean_of_three() {
        assert_eq!(mean(&[1.0, 2.0, 6.0]), Some(3.0));
        assert_eq!(mean(&[]), None);
    }

    #[test]
    fn median_even_and_odd() {
        assert_eq!(median(&mut [3.0, 1.0, 2.0]), Some(2.0));
        assert_eq!(median(&mut [4.0, 1.0, 3.0, 2.0]), Some(2.5));
    }

    #[test]
    fn histogram_bins() {
        let mut h = Histogram::new(4, 0.0, 8.0).unwrap();
        for x in [0.5, 1.9, 2.0, 7.99] {
            h.add(x).unwrap();
        }
        assert_eq!((h.count(0), h.count(1), h.count(3)), (Some(2), Some(1), Some(1)));
        assert_eq!(h.add(8.0), Err(AddError::OutOfRange));
        assert!(Histogram::new(0, 0.0, 1.0).is_none());
    }
}
