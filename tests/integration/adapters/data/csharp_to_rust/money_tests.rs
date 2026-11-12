//! The tests of tests/Shop.Tests/MoneyTests.cs, translated from C#.
use codecoremorph_runtime::Decimal;
use shop_rs::money::Money;

fn eur(amount: &str) -> Money {
    Money::new(Decimal::parse(amount).unwrap(), Some("EUR")).unwrap()
}

#[test]
fn adding_keeps_the_scale() {
    assert_eq!("2.10 EUR", eur("1.10").add(&eur("1.0")).unwrap().to_string());
}

#[test]
fn times_multiplies_the_amount() {
    assert_eq!("7.50 EUR", eur("2.50").times(3).unwrap().to_string());
}

#[test]
fn currencies_do_not_mix() {
    let usd = Money::new(Decimal::ONE, Some("USD")).unwrap();
    let e = eur("1").add(&usd).unwrap_err();
    assert!(e.is("System.InvalidOperationException"));
    assert!(e.message().contains("USD"));
}

fn percent_is_unrounded(percent: i32, expected: &str) {
    assert_eq!(expected, eur("3.33").percent(percent).unwrap().to_string());
}

#[test]
fn percent_is_unrounded_10() {
    percent_is_unrounded(10, "0.333 EUR");
}

#[test]
fn percent_is_unrounded_15() {
    percent_is_unrounded(15, "0.4995 EUR");
}

#[test]
fn percent_is_unrounded_33() {
    percent_is_unrounded(33, "1.0989 EUR");
}
