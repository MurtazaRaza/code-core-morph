// The program Shop.Cli, ported from C# by hand for the test.
#include "shop_cli_main.hpp"

#include <iostream>

#include "money.hpp"

int shop_cli_main(const std::vector<std::string>& args)
{
    const int count = static_cast<int>(args.size());
    std::cout << shop::Money(ccm::Decimal::parse("2.50"), "EUR").times(count).to_string() << '\n';
    return count;
}
