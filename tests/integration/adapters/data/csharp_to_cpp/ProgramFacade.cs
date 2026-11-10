using CodeCoreMorph.Interop;

namespace Shop.Cli
{
    public static class Program
    {
        public static int Main(string[] args)
        {
            CcmInterop.Check(CcmNative.shop_shop_cli_main(args, args.Length, out var code));
            return code;
        }
    }
}
