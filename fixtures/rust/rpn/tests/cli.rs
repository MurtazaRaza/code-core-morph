//! The rpn program, run as a user would.

use std::process::Command;

fn rpn(args: &[&str]) -> (String, String, i32) {
    let out = Command::new(env!("CARGO_BIN_EXE_rpn"))
        .args(args)
        .output()
        .expect("rpn runs");
    (
        String::from_utf8(out.stdout).unwrap(),
        String::from_utf8(out.stderr).unwrap(),
        out.status.code().unwrap_or(-1),
    )
}

#[test]
fn evaluates_each_argument() {
    assert_eq!(rpn(&["3 4 +", "2 3 *", "1 4 /"]), ("7\n6\n0.25\n".to_string(), String::new(), 0));
}

#[test]
fn reports_errors_and_keeps_going() {
    let (out, err, status) = rpn(&["1 0 /", "2 2 +"]);
    assert_eq!(out, "4\n");
    assert_eq!(err, "rpn: 1 0 /: division by zero\n");
    assert_eq!(status, 1);
}

#[test]
fn reduces_numbers() {
    assert_eq!(rpn(&["--reduce", "mean", "1", "2", "6"]).0, "3\n");
    assert_eq!(rpn(&["--reduce", "max", "-1", "-7"]).0, "-1\n");
    assert_eq!(rpn(&["--reduce", "median", "1"]).2, 2);
    assert_eq!(rpn(&["--reduce", "max"]), (String::new(), "rpn: max of no numbers\n".to_string(), 1));
}
