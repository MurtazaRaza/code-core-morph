//! Reductions over lists of numbers, chosen by name.

/// Reduces a list of numbers to one.
pub trait Reducer {
    fn name(&self) -> &'static str;
    /// None for an empty list, where the reduction has no value.
    fn reduce(&self, xs: &[f64]) -> Option<f64>;
}

pub struct Sum;
pub struct Mean;
pub struct Max;

impl Reducer for Sum {
    fn name(&self) -> &'static str {
        "sum"
    }

    fn reduce(&self, xs: &[f64]) -> Option<f64> {
        Some(xs.iter().sum())
    }
}

impl Reducer for Mean {
    fn name(&self) -> &'static str {
        "mean"
    }

    fn reduce(&self, xs: &[f64]) -> Option<f64> {
        if xs.is_empty() {
            None
        } else {
            Some(xs.iter().sum::<f64>() / xs.len() as f64)
        }
    }
}

impl Reducer for Max {
    fn name(&self) -> &'static str {
        "max"
    }

    fn reduce(&self, xs: &[f64]) -> Option<f64> {
        xs.iter().copied().fold(None, |best, x| match best {
            Some(b) if b >= x => Some(b),
            _ => Some(x),
        })
    }
}

/// The reducer of that name: `sum`, `mean` or `max`.
pub fn by_name(name: &str) -> Option<Box<dyn Reducer>> {
    match name {
        "sum" => Some(Box::new(Sum)),
        "mean" => Some(Box::new(Mean)),
        "max" => Some(Box::new(Max)),
        _ => None,
    }
}

/// Each value of `xs` that `keep` accepts, in order.
pub fn filtered<F>(xs: &[f64], keep: F) -> Vec<f64>
where
    F: Fn(f64) -> bool,
{
    xs.iter().copied().filter(|&x| keep(x)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reducers_by_name() {
        let xs = [2.0, 8.0, 5.0];
        assert_eq!(by_name("sum").unwrap().reduce(&xs), Some(15.0));
        assert_eq!(by_name("mean").unwrap().reduce(&xs), Some(5.0));
        assert_eq!(by_name("max").unwrap().reduce(&xs), Some(8.0));
        assert_eq!(by_name("max").unwrap().name(), "max");
        assert!(by_name("median").is_none());
    }

    #[test]
    fn empty_lists() {
        assert_eq!(Sum.reduce(&[]), Some(0.0));
        assert_eq!(Mean.reduce(&[]), None);
        assert_eq!(Max.reduce(&[]), None);
    }

    #[test]
    fn filtering_with_a_closure() {
        let limit = 4.0;
        assert_eq!(filtered(&[1.0, 5.0, 3.0, 9.0], |x| x < limit), vec![1.0, 3.0]);
    }
}
