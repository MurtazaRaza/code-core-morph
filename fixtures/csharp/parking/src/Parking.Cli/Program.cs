using System;
using System.Collections.Generic;
using System.Globalization;

namespace Parking.Cli
{
    /// <summary>Parking.Cli KIND:PLATE:MINUTES...: parks each vehicle at minute 0, lets each leave
    /// after its minutes on the hourly plan, and prints each fee and the takings.</summary>
    internal static class Program
    {
        private static int Main(string[] args)
        {
            if (args.Length == 0)
            {
                Console.Error.WriteLine("usage: Parking.Cli KIND:PLATE:MINUTES...");
                return 2;
            }
            var garage = new Garage(Limits.MaxCapacity, new HourlyRate(new Money(2.50m), new Money(20m)));
            var stays = new List<KeyValuePair<string, int>>();
            try
            {
                foreach (string arg in args)
                {
                    string[] parts = arg.Split(':');
                    int minutes;
                    if (parts.Length != 3
                        || !int.TryParse(parts[2], NumberStyles.None, CultureInfo.InvariantCulture, out minutes))
                    {
                        Console.Error.WriteLine("not KIND:PLATE:MINUTES: " + arg);
                        return 2;
                    }
                    Vehicle vehicle = Make(parts[0], parts[1]);
                    if (vehicle == null)
                    {
                        Console.Error.WriteLine("no such kind of vehicle: " + parts[0]);
                        return 2;
                    }
                    garage.Enter(vehicle, 0);
                    stays.Add(new KeyValuePair<string, int>(vehicle.Plate, minutes));
                }
                foreach (KeyValuePair<string, int> stay in stays)
                {
                    Money fee = garage.Leave(stay.Key, stay.Value);
                    Console.WriteLine(stay.Key + " paid " + fee);
                }
            }
            catch (ArgumentException e)
            {
                Console.Error.WriteLine(e.Message);
                return 1;
            }
            catch (InvalidOperationException e)
            {
                Console.Error.WriteLine(e.Message);
                return 1;
            }
            Console.WriteLine("takings " + garage.Takings);
            return 0;
        }

        private static Vehicle Make(string kind, string plate)
        {
            switch (kind)
            {
                case "car":
                    return new Car(plate);
                case "motorcycle":
                    return new Motorcycle(plate);
                default:
                    return null;
            }
        }
    }
}
