//! shop.Money: an amount of money in cents, with its currency. Immutable.
use codecoremorph_java::lang as java;
use codecoremorph_java::Exception;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Money {
    cents: i64,
    currency: String,
}

fn same_currency(a: &str, b: &str) -> Result<(), Exception> {
    if a == b {
        Ok(())
    } else {
        Err(Exception::illegal_argument(format!("currencies differ: {a} and {b}")))
    }
}

impl Money {
    /// IllegalArgumentException for a currency that hasn't three letters (UTF-16 units).
    pub fn new(cents: i64, currency: impl Into<String>) -> Result<Money, Exception> {
        let currency = currency.into();
        if java::string_length(&currency) != 3 {
            return Err(Exception::illegal_argument(format!(
                "a currency has three letters: {currency}"
            )));
        }
        Ok(Money { cents, currency })
    }

    /// Parses "12.34 EUR".
    pub fn parse(text: &str) -> Result<Money, Exception> {
        let parts = text::split_on_space(text::trim(text));
        if parts.len() != 2 {
            return Err(Exception::illegal_argument(format!("not an amount: {text}")));
        }
        let mut number = parts[0].as_str();
        let negative = number.starts_with('-');
        if negative {
            number = &number[1..];
        }
        let dot = number.find('.');
        let whole = text::parse_long(dot.map_or(number, |d| &number[..d]))?;
        let mut frac = 0;
        if let Some(d) = dot {
            let f = &number[d + 1..];
            if java::string_length(f) != 2 {
                return Err(Exception::number_format(format!("two decimals: {text}")));
            }
            frac = text::parse_long(f)?;
        }
        let cents = whole.wrapping_mul(100).wrapping_add(frac);
        Money::new(if negative { cents.wrapping_neg() } else { cents }, parts[1].clone())
    }

    pub fn cents(&self) -> i64 {
        self.cents
    }

    pub fn currency(&self) -> &str {
        &self.currency
    }

    pub fn plus(&self, other: &Money) -> Result<Money, Exception> {
        same_currency(&self.currency, &other.currency)?;
        Ok(Money { cents: self.cents.wrapping_add(other.cents), currency: self.currency.clone() })
    }

    pub fn times(&self, n: i32) -> Money {
        Money { cents: self.cents.wrapping_mul(i64::from(n)), currency: self.currency.clone() }
    }

    /// This amount's percentage, rounded half up to the cent.
    pub fn percent(&self, percent: i32) -> Money {
        let scaled = self.cents.wrapping_mul(i64::from(percent));
        let rounded = if scaled >= 0 {
            scaled.wrapping_add(50) / 100
        } else {
            (scaled.wrapping_neg().wrapping_add(50) / 100).wrapping_neg()
        };
        Money { cents: rounded, currency: self.currency.clone() }
    }

    pub fn is_zero(&self) -> bool {
        self.cents == 0
    }

    pub fn compare_to(&self, other: &Money) -> Result<i32, Exception> {
        same_currency(&self.currency, &other.currency)?;
        Ok(match self.cents.cmp(&other.cents) {
            std::cmp::Ordering::Less => -1,
            std::cmp::Ordering::Equal => 0,
            std::cmp::Ordering::Greater => 1,
        })
    }

    /// Money.hashCode, as Java computes it.
    pub fn hash_code(&self) -> i32 {
        let mut h: i32 = 17;
        h = h.wrapping_mul(31).wrapping_add(java::long_hash(self.cents));
        h = h.wrapping_mul(31).wrapping_add(java::string_hash(&self.currency));
        h
    }

    pub fn to_java_string(&self) -> String {
        // Math.abs(Long.MIN_VALUE) is Long.MIN_VALUE, as in Java.
        let abs = self.cents.wrapping_abs();
        let mut frac = (abs % 100).to_string();
        if java::string_length(&frac) < 2 {
            frac = format!("0{frac}");
        }
        format!("{}{}.{} {}", if self.cents < 0 { "-" } else { "" }, abs / 100, frac, self.currency)
    }
}

/// What the Java's String methods do, where the port needs them.
mod text {
    use codecoremorph_java::Exception;
    /// String.trim: the characters at most U+0020 off both ends.
    pub fn trim(s: &str) -> &str {
        s.trim_matches(|c: char| c <= '\u{20}')
    }

    /// String.split(" "): on each space, the trailing empty strings removed.
    pub fn split_on_space(s: &str) -> Vec<String> {
        let mut parts: Vec<String> = s.split(' ').map(str::to_string).collect();
        if parts.len() == 1 {
            return parts;
        }
        while parts.last().is_some_and(String::is_empty) {
            parts.pop();
        }
        parts
    }

    /// Long.parseLong, as Java 8 throws NumberFormatException.
    pub fn parse_long(s: &str) -> Result<i64, Exception> {
        let fail = || Exception::number_format(format!("For input string: \"{s}\""));
        let digits = s.strip_prefix(['-', '+']).unwrap_or(s);
        if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
            return Err(fail());
        }
        s.parse::<i64>().map_err(|_| fail())
    }
}
