use wf_rs::wf::Table;

#[test]
fn a_new_table_is_empty() {
    assert!(Table::new().top(3).is_empty());
}
