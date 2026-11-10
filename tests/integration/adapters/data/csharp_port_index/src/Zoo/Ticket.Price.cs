namespace Zoo
{
    public partial class Ticket
    {
        public decimal Price()
        {
            return TicketKind == Kind.Adult ? 12.50m : 6.25m;
        }
    }
}
