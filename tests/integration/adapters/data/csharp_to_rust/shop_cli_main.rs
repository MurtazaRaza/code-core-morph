//! The program Shop.Cli, ported from C#: it prints the price of as many 2.50 EUR items as it
//! has arguments, and exits with their count.
use codecoremorph_runtime::Decimal;

use crate::money::Money;

pub fn shop_cli_main(args: &[String]) -> i32 {
    let count = i32::try_from(args.len()).unwrap_or(i32::MAX);
    let total = Decimal::parse("2.50")
        .and_then(|price| Money::new(price, Some("EUR")))
        .and_then(|price| price.times(count));
    match total {
        Ok(total) => {
            println!("{total}");
            count
        }
        Err(e) => {
            eprintln!("Unhandled exception. {}: {e}", e.dotnet_type());
            134
        }
    }
}
