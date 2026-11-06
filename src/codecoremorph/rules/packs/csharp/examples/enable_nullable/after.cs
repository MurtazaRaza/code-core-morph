#nullable enable
public class Catalog
{
    private Item? cached;

    public Item? Find(string sku)
    {
        foreach (var item in items)
        {
            if (item.Sku == sku)
            {
                return item;
            }
        }
        return null;
    }
}
