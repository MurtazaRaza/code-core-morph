public string Describe(int n)
{
    return n switch
    {
        0 => "none",
        1 => "one",
        _ => "many",
    };
}
