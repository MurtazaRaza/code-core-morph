//! Shop.Money, ported from C#.
use std::collections::hash_map::DefaultHasher;
use std::fmt;
use std::hash::{Hash, Hasher};

use codecoremorph_runtime::{Decimal, Exception, Result};

/// An amount of money in one currency. `Money::default()` is C#'s `default(Money)`: no
/// currency.
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
pub struct Money {
    amount: Decimal,
    currency: Option<String>,
}

impl Money {
    pub fn new(amount: Decimal, currency: Option<&str>) -> Result<Money> {
        let currency = currency.ok_or_else(|| Exception::argument_null("currency"))?;
        if currency.encode_utf16().count() != 3 {
            return Err(Exception::argument("a currency is a three-letter code", "currency"));
        }
        Ok(Money { amount, currency: Some(currency.to_string()) })
    }

    pub fn amount(&self) -> Decimal {
        self.amount
    }

    pub fn currency(&self) -> Option<&str> {
        self.currency.as_deref()
    }

    pub fn zero(currency: Option<&str>) -> Result<Money> {
        Money::new(Decimal::ZERO, currency)
    }

    pub fn add(&self, other: &Money) -> Result<Money> {
        self.check_currency(other)?;
        Money::new(self.amount.checked_add(other.amount)?, self.currency())
    }

    pub fn subtract(&self, other: &Money) -> Result<Money> {
        self.check_currency(other)?;
        Money::new(self.amount.checked_sub(other.amount)?, self.currency())
    }

    pub fn times(&self, quantity: i32) -> Result<Money> {
        Money::new(self.amount.checked_mul(Decimal::from(quantity))?, self.currency())
    }

    /// The given percentage of this amount, unrounded.
    pub fn percent(&self, percent: i32) -> Result<Money> {
        let share = self.amount.checked_mul(Decimal::from(percent))?.checked_div(Decimal::from(100))?;
        Money::new(share, self.currency())
    }

    /// C#'s `GetHashCode`: equal values hash equally (not the C# numbers).
    pub fn hash_code(&self) -> i32 {
        let mut hasher = DefaultHasher::new();
        self.hash(&mut hasher);
        (hasher.finish() & 0x7fff_ffff) as i32
    }

    fn check_currency(&self, other: &Money) -> Result<()> {
        if other.currency != self.currency {
            return Err(Exception::invalid_operation(format!(
                "cannot combine {} with {}",
                other.currency().unwrap_or(""),
                self.currency().unwrap_or("")
            )));
        }
        Ok(())
    }
}

impl fmt::Display for Money {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} {}", self.amount, self.currency().unwrap_or(""))
    }
}
