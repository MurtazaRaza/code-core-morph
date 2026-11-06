public int Count(string path)
{
    using (var reader = new StreamReader(path))
    {
        return reader.ReadToEnd().Length;
    }
}
