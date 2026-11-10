using System;

namespace Shop.Cli
{
    public static class Program
    {
        public static int Main(string[] args)
        {
            Console.WriteLine(new Money(2.50m, "EUR").Times(args.Length));
            return args.Length;
        }
    }
}
