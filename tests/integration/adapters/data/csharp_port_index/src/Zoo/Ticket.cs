namespace Zoo
{
    /// <summary>A partial class in two files, with nested types.</summary>
    public partial class Ticket
    {
        public enum Kind
        {
            Adult,
            Child,
        }

        public struct Stub
        {
            public int Number;
        }

        public Ticket(Kind kind)
        {
            TicketKind = kind;
        }

        public Kind TicketKind { get; private set; }
    }
}
