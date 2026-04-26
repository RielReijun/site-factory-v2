<section className="py-16 md:py-24 bg-background">
  <div className="container mx-auto px-4">
    [[ ?headline ]]
    <div className="text-center mb-12 md:mb-16">
      <h2 className="font-heading text-3xl md:text-4xl font-bold mb-3">[[ headline ]]</h2>
    </div>
    [[ / ]]

    <div className="w-full overflow-x-auto rounded-xl border border-border shadow-sm">
      <table className="w-full min-w-[480px] text-sm">
        <thead>
          <tr className="bg-muted border-b border-border">
            <th className="text-left px-6 py-4 font-heading font-semibold text-foreground w-1/3">
              Plan
            </th>
            <th className="text-left px-6 py-4 font-heading font-semibold text-foreground w-1/4">
              Prijs
            </th>
            <th className="text-left px-6 py-4 font-heading font-semibold text-foreground">
              Omschrijving
            </th>
          </tr>
        </thead>
        <tbody>
          [[ *plans ]]
          <tr className="border-b border-border last:border-0 bg-card hover:bg-muted/50 transition-colors">
            <td className="px-6 py-5">
              <span className="font-heading font-semibold text-base text-foreground">
                [[ .name ]]
              </span>
            </td>
            <td className="px-6 py-5">
              <span className="inline-block bg-primary/10 text-primary font-semibold text-sm px-3 py-1 rounded-full">
                [[ .price ]]
              </span>
            </td>
            <td className="px-6 py-5">
              <p className="text-muted-foreground leading-relaxed">
                [[ .description ]]
              </p>
            </td>
          </tr>
          [[ / ]]
        </tbody>
      </table>
    </div>
  </div>
</section>