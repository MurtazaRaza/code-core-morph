//! Descriptive statistics over slices, and a fixed-width histogram.

/// The arithmetic mean, or None for an empty slice.
pub fn mean(xs: &[f64]) -> Option<f64> {
    if xs.is_empty() {
        return None;
    }
    Some(xs.iter().sum::<f64>() / xs.len() as f64)
}

/// The median; sorts `xs` in place. None for an empty slice.
pub fn median(xs: &mut [f64]) -> Option<f64> {
    if xs.is_empty() {
        return None;
    }
    xs.sort_by(|a, b| a.total_cmp(b));
    let mid = xs.len() / 2;
    Some(if xs.len() % 2 == 0 { (xs[mid - 1] + xs[mid]) / 2.0 } else { xs[mid] })
}

/// Counts of values in `bins` equal-width bins over `[lo, hi)`.
#[derive(Debug)]
pub struct Histogram {
    lo: f64,
    hi: f64,
    counts: Vec<u32>,
}

/// Why a value couldn't be added.
#[derive(Debug, PartialEq, Eq)]
pub enum AddError {
    OutOfRange,
}

impl Histogram {
    /// None when there are no bins or the range is empty.
    pub fn new(bins: usize, lo: f64, hi: f64) -> Option<Self> {
        if bins == 0 || !(lo < hi) {
            return None;
        }
        Some(Self { lo, hi, counts: vec![0; bins] })
    }

    pub fn add(&mut self, x: f64) -> Result<(), AddError> {
        if !(x >= self.lo && x < self.hi) {
            return Err(AddError::OutOfRange);
        }
        let width = (self.hi - self.lo) / self.counts.len() as f64;
        let i = (((x - self.lo) / width) as usize).min(self.counts.len() - 1);
        self.counts[i] += 1;
        Ok(())
    }

    pub fn count(&self, bin: usize) -> Option<u32> {
        self.counts.get(bin).copied()
    }

    pub fn bins(&self) -> usize {
        self.counts.len()
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
