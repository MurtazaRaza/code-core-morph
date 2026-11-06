public string Describe(int n)
{
    switch (n)
    {
        case 0:
            return "none";
        case 1:
            return "one";
        default:
            return "many";
    }
}
