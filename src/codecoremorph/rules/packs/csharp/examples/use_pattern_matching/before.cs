public override bool Equals(object obj)
{
    if (!(obj is Item))
    {
        return false;
    }
    Item other = (Item)obj;
    return sku == other.sku;
}
