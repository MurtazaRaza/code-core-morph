namespace Parking
{
    public sealed class Motorcycle : Vehicle
    {
        public Motorcycle(string plate)
            : base(plate)
        {
        }

        public override int Wheels
        {
            get { return 2; }
        }

        public override string Kind
        {
            get { return "motorcycle"; }
        }

        public override decimal SizeFactor
        {
            get { return 0.5m; }
        }
    }
}
