namespace Parking
{
    public sealed class Car : Vehicle
    {
        public Car(string plate)
            : base(plate)
        {
        }

        public override int Wheels
        {
            get { return 4; }
        }

        public override string Kind
        {
            get { return "car"; }
        }
    }
}
