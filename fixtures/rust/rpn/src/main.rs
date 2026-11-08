//! rpn: evaluates each argument as a reverse Polish expression, or reduces numbers:
//!
//!     rpn '3 4 +' '2 3 *'        prints 7 and 6, one per line
//!     rpn --reduce mean 1 2 6    prints 3
//!
//! Exits with 1 when an expression has no value, 2 on a usage error.

use std::env;
use std::process;

use rpn::eval::eval_str;
use rpn::reduce::by_name;

fn usage() -> ! {
    eprintln!("usage: rpn EXPRESSION...\n       rpn --reduce sum|mean|max NUMBER...");
    process::exit(2);
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.is_empty() {
        usage();
    }
    if args[0] == "--reduce" {
        let reducer = match args.get(1).and_then(|name| by_name(name)) {
            Some(r) => r,
            None => usage(),
        };
        let mut xs = Vec::new();
        for arg in &args[2..] {
            match arg.parse::<f64>() {
                Ok(x) => xs.push(x),
                Err(_) => usage(),
            }
        }
        match reducer.reduce(&xs) {
            Some(value) => println!("{}", value),
            None => {
                eprintln!("rpn: {} of no numbers", reducer.name());
                process::exit(1);
            }
        }
        return;
    }
    let mut status = 0;
    for expr in &args {
        match eval_str(expr) {
            Ok(value) => println!("{}", value),
            Err(e) => {
                eprintln!("rpn: {}: {}", expr, e);
                status = 1;
            }
        }
    }
    process::exit(status);
}
