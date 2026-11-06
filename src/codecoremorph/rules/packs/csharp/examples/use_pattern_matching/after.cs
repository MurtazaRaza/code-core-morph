public override bool Equals(object obj)
{
    if (obj is not Item other)
    {
        return false;
    }
    return sku == other.sku;
}
