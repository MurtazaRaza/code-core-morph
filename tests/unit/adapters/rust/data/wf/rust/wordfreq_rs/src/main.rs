//! Prints the most frequent words of its standard input (the recorded SCIP index,
//! ../../wordfreq.scip, is of the full program).
use std::io::Read;
use std::process::ExitCode;

use wf_rs::conf;
use wf_rs::wf::Table;

fn usage() -> ExitCode {
    eprintln!("usage: wordfreq [-k] [count]");
    ExitCode::from(2)
}

fn main() -> ExitCode {
    let mut count = 10;
    for arg in std::env::args().skip(1) {
        if arg == "-k" {
            conf::set_fold_case(false);
        } else if let Ok(n) = arg.parse() {
            count = n;
        } else {
            return usage();
        }
    }
    let mut text = Vec::new();
    std::io::stdin().read_to_end(&mut text).ok();
    let mut table = Table::new();
    table.count_text(&text);
    for word in table.top(count) {
        println!("{:>7} {}", word.count(), String::from_utf8_lossy(word.text()));
    }
    ExitCode::SUCCESS
}
