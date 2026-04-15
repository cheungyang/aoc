import discord
from discord.ui import View, Button, Select

class PollButtonView(View):
    def __init__(self, poll_data, channel):
        super().__init__(timeout=None) # Persistent view
        self.poll_data = poll_data
        self.channel = channel
        
        for option in poll_data["options"]:
            button = Button(label=option["text"], emoji=option["emoji"] if option["emoji"] else None)
            button.callback = self.create_callback(option["response"])
            self.add_item(button)

    def create_callback(self, response_text):
        async def callback(interaction: discord.Interaction):
            # Send the response text to the channel
            # We mention the user to make it clear who voted
            message_text = f"{interaction.user.mention}: {response_text}"
            await self.channel.send(message_text)
            # Acknowledge the interaction
            await interaction.response.defer()
        return callback

class PollSelectView(View):
    def __init__(self, poll_data, channel):
        super().__init__(timeout=None)
        self.poll_data = poll_data
        self.channel = channel
        
        options = []
        for i, option in enumerate(poll_data["options"]):
            options.append(discord.SelectOption(
                label=option["text"],
                value=str(i), # Use index as value
                emoji=option["emoji"] if option["emoji"] else None
            ))
            
        select = Select(
            placeholder="Select options...",
            min_values=1,
            max_values=len(options),
            options=options
        )
        select.callback = self.callback
        self.add_item(select)

    async def callback(self, interaction: discord.Interaction):
        # Get selected options
        selected_indices = [int(val) for val in interaction.data["values"]]
        responses = [self.poll_data["options"][i]["response"] for i in selected_indices]
        
        # Combine responses
        message_text = f"{interaction.user.mention}: " + ", ".join(responses)
        await self.channel.send(message_text)
        # Acknowledge the interaction
        await interaction.response.defer()
