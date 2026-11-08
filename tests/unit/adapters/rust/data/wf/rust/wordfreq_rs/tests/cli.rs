use std::process::Command;

#[test]
fn an_unknown_option_shows_the_usage() {
    let out = Command::new(env!("CARGO_BIN_EXE_wordfreq")).arg("-x").output().unwrap();
    assert_eq!(out.status.code(), Some(2));
}
